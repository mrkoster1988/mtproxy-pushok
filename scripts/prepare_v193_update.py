"""Подготовка установленной v1.92 с исправлениями из чата к штатному обновлению."""
import os
import subprocess
import sys

# Промежуточные версии файлов, входивших в исправления для v1.92.
KNOWN_HOTFIXES = {
    'api/app/automatic_moderation.py': {
        '0f33c28e8db61a0e860dbf274b7b4517ee9ed2e6',
        '8ea2ffbeb6fd35c10428da90663191d35229b375',
        'f5efbc230e19b1cc9a31e8fb87be1cf00f6200a2',
    },
    'api/app/main.py': {
        '588a8e4b579977cb20d9e9ca93568727dcd94411',
        '674b9f6dd3e21300c7ce84efd58da486e9d8b4df',
        'c06d318b3a9c9ef8da0c3de6ab4749211327e8f6',
    },
    'api/web/index.html': {
        '334d960ceb5255ed9b8c690eaa5ee2ed5e9381f8',
        'd1767a4bcd5f19944ce17772d71f397a22b6720e',
        'fc4f3111f75e02954558a988005c8c1a1689f9dc',
    },
    'api/web/mobile-views.js': {
        'a9b40b5e830cbd8c387a731ad89dc819512ed15e',
        'a7f580ba662bc88c811733ab0e79caf4a353f958',
    },
    'api/web/moderation.css': {
        '592309966e33c6aaadfb4400b6b791f33a98bac7',
    },
    'tests/test_automatic_moderation.py': {
        '0d86ea97c07a849fe93194cc13aaba9fdea49e16',
        '0f4835fd05dbdab9e19cab316a6af9cb02bf530d',
        '6719f796758f6054886647889c5ded9423ec5b33',
        'eadb5722e481340fb8ca45f0a689ae75a0101aad',
    },
}


def git(*args):
    return subprocess.check_output(['git', '-c', 'user.name=Pushok', '-c', 'user.email=pushok@localhost', *args])


def tree(ref):
    entries = {}
    for record in git('ls-tree', '-rz', ref).split(b'\0'):
        if record:
            metadata, path = record.split(b'\t', 1)
            mode, _, oid = metadata.split()
            entries[os.fsdecode(path)] = (mode, oid.decode())
    return entries


def main():
    target = git('rev-parse', 'FETCH_HEAD^{commit}').decode().strip()
    if 'Версия проекта: v1.93.'.encode() not in git('show', target + ':README.md').splitlines():
        sys.exit('Для этой команды требуется выпуск v1.93 в FETCH_HEAD.')
    git('merge-base', '--is-ancestor', 'HEAD', target)
    if git('diff', '--cached', '--name-only', '-z'):
        sys.exit('Есть подготовленные к коммиту файлы. Сначала проверьте git status.')
    snapshot = git('stash', 'create').decode().strip()
    if snapshot:
        old, local, release = tree('HEAD'), tree(snapshot), tree(target)
        for path in old.keys() | local.keys():
            entry = local.get(path)
            if entry == old.get(path) or entry == release.get(path):
                continue
            if entry and entry[0] == b'100644' and entry[1] in KNOWN_HOTFIXES.get(path, ()):
                continue
            sys.exit('Обнаружена своя правка: ' + path + '. Рабочие файлы сохранены.')
        git('stash', 'push', '-m', 'Локальные исправления перед v1.93')
    print('Рабочая копия подготовлена. В кабинете нажмите «Проверить» и «Установить v1.93».')


if __name__ == '__main__':
    main()
