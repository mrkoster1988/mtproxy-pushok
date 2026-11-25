"""Run as root on the cabinet VPS with Android config and sender JSON paths."""
import argparse
import base64
import fcntl
import sys
from urllib.error import HTTPError
from urllib.request import Request
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.request import urlopen


def atomic_write(path, data, uid=0, gid=0, mode=0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        name = tmp.name
        try:
            tmp.write(data)
            tmp.flush()
            os.fchown(tmp.fileno(), uid, gid)
            os.fchmod(tmp.fileno(), mode)
            os.fsync(tmp.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)


def settings(android, sender):
    project = android["project_info"]
    client, = [c for c in android["client"] if
               c["client_info"]["android_client_info"]["package_name"] == "shop.pushok.flow"]
    values = {"FLOW_FCM_PROJECT_ID": project["project_id"],
              "FLOW_FCM_SENDER_ID": project["project_number"],
              "FLOW_FCM_ANDROID_APP_ID": client["client_info"]["mobilesdk_app_id"],
              "FLOW_FCM_ANDROID_API_KEY": client["api_key"][0]["current_key"]}
    if (values["FLOW_FCM_PROJECT_ID"] != "pushok-flow" or
            sender.get("project_id") != project["project_id"] or
            sender.get("type") != "service_account" or
            sender.get("token_uri") != "https://oauth2.googleapis.com/token" or
            not sender.get("private_key") or not sender.get("client_email") or
            not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_:\-]+", v)
                    for v in values.values())):
        raise ValueError("Проверьте проект и формат двух JSON-файлов.")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("android", type=Path)
    parser.add_argument("sender", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/opt/mtproxy-pushok"))
    args = parser.parse_args()
    sender_data = args.sender.read_bytes()
    values = settings(json.loads(args.android.read_bytes()), json.loads(sender_data))
    if os.geteuid() != 0:
        raise SystemExit("Запустите от root.")
    root = args.root.resolve()
    env = root / ".env"
    original = env.read_bytes()
    info = env.stat()
    compose = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.flow-push.yaml"]
    def run(*args):
        subprocess.run(args, cwd=root, check=True)

    secrets = Path("/etc/pushok-flow")
    secrets.mkdir(mode=0o700, exist_ok=True)
    credential = secrets / "fcm-sender.json"
    updates = {**values, "FLOW_FCM_CREDENTIAL_FILE": str(credential)}
    lines = [line for line in original.decode().splitlines() if
             not any(re.match(r"^\s*(?:export\s+)?" + key + r"\s*=", line) for key in updates)]
    lines.extend(f"{key}={value}" for key, value in updates.items())
    run("docker", "compose", "exec", "-T", "db", "sh", "-ec",
        """psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c 'SELECT 1 FROM flow_push_events LIMIT 0'""")
    atomic_write(credential, sender_data, uid=10001)
    atomic_write(env, ("\n".join(lines) + "\n").encode(), info.st_uid, info.st_gid, info.st_mode & 0o777)
    run(*compose, "config", "--quiet")
    run(*compose, "up", "-d", "--build", "--no-deps", "api", "flow-push")
    run(*compose, "exec", "-T", "flow-push", "python", "-c",
        "import google.auth; from google.auth.transport.requests import Request; "
        "c,_=google.auth.default(scopes=['https://www.googleapis.com/auth/firebase.messaging']); "
        "c.refresh(Request())")
    expected = dict(zip(("project", "sender", "app", "key"), values.values()))
    for _ in range(20):
        try:
            with urlopen("http://127.0.0.1:8000/api/v1/flow/push-config", timeout=3) as reply:
                if json.load(reply) == expected:
                    print("Конфигурация API и получение токена отправителя проверены.")
                    return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit("Проверка /push-config не прошла.")


def provision():
    """Obtain both configs on the VPS using a temporary Cloud CLI session."""
    root = Path("/opt/mtproxy-pushok")
    if os.geteuid() or not (root / "compose.flow-push.yaml").is_file():
        raise SystemExit("Нужен root на VPS с установленным обновлением Pushok Flow.")
    os.umask(0o077)
    project = "pushok-flow"
    account = "flow-vps-sender@" + project + ".iam.gserviceaccount.com"
    image = "gcr.io/google.com/cloudsdktool/google-cloud-cli:stable"
    directory = Path("/etc/pushok-flow")
    directory.mkdir(mode=0o700, exist_ok=True)
    credential = directory / "fcm-sender.json"
    with (directory / ".setup.lock").open("a") as lock, tempfile.TemporaryDirectory() as tmp:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def cloud(*args, interactive=False, capture=False):
            cmd = ["docker", "run", "--rm", "-v", tmp + ":/config",
                   "-e", "CLOUDSDK_CONFIG=/config", "-e", "CLOUDSDK_CORE_PROJECT=" + project]
            if interactive:
                cmd.append("-it")
            return subprocess.run([*cmd, image, "gcloud", *args], check=True,
                                  stdout=subprocess.PIPE if capture else None)
        cloud("auth", "login", "--no-launch-browser", interactive=True)
        token = cloud("auth", "print-access-token", capture=True).stdout.decode().strip()
        app = "1:779628696156:android:ab2388d962101088328c99"
        request = Request("https://firebase.googleapis.com/v1beta1/projects/" + project +
                          "/androidApps/" + app + "/config", headers={"Authorization": "Bearer " + token,
                                                             "X-Goog-User-Project": project})
        android = Path(tmp) / "android.json"
        for attempt in range(5):
            try:
                with urlopen(request, timeout=30) as response:
                    android.write_bytes(base64.b64decode(json.load(response)["configFileContents"], validate=True))
                break
            except HTTPError as exc:
                try:
                    error = json.loads(exc.read()).get("error", {})
                except ValueError:
                    error = {}
                disabled = any(d.get("reason") == "SERVICE_DISABLED" and
                               d.get("metadata", {}).get("service") == "firebase.googleapis.com"
                               for d in error.get("details", []) if isinstance(d, dict))
                if disabled and exc.code == 403 and attempt < 4:
                    if attempt == 0:
                        cloud("services", "enable", "firebase.googleapis.com")
                    time.sleep(3 * (attempt + 1))
                    continue
                raise SystemExit(f"Firebase HTTP {exc.code}: {error.get('message') or exc.reason}") from None
        if credential.exists() and json.loads(credential.read_bytes()).get("client_email") != account:
            raise SystemExit("Уже установлен другой отправитель. Существующий ключ сохранён.")
        accounts = cloud("iam", "service-accounts", "list", "--format=value(email)", capture=True)
        if account not in accounts.stdout.decode().splitlines():
            cloud("iam", "service-accounts", "create", "flow-vps-sender", "--display-name=Pushok Flow VPS")
        cloud("projects", "add-iam-policy-binding", project, "--member=serviceAccount:" + account,
              "--role=roles/firebasecloudmessaging.admin", "--condition=None", "--format=none")
        if not credential.exists():
            cloud("iam", "service-accounts", "keys", "create", "/config/sender.json",
                  "--iam-account=" + account)
            key = (Path(tmp) / "sender.json").read_bytes()
            settings(json.loads(android.read_bytes()), json.loads(key))
            atomic_write(credential, key, uid=10001)
        sys.argv[1:] = [str(android), str(credential)]
        main()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        provision()
    else:
        main()
