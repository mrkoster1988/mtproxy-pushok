"""Host-only протокол для подготовленных тегов. Не подтверждает наблюдение показа."""
import json
import os
import sys
import hashlib
import psycopg
from psycopg.rows import dict_row


def desired(conn, host, port):
    node = conn.execute("SELECT id,status,archived_at,target_engine FROM proxy_nodes WHERE host=%s AND port=%s", (host,port)).fetchone()
    if not node:
        return {"state":"idle"}
    result = {"state":"idle", "node_id":str(node['id'])}
    if node['status'] != 'active' or node['archived_at'] or node['target_engine'] not in (None,'official_mtproxy'):
        return result
    # Ручной активный показ не перехватывается автоматикой.
    active = conn.execute("SELECT 1 FROM campaigns c WHERE proxy_node_id=%s AND placement_status='active' AND scheduled_end_at>now() AND NOT EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied') LIMIT 1", (node['id'],)).fetchone()
    if active:
        return result
    rows = conn.execute("""SELECT c.id,b.ad_tag,c.scheduled_end_at
      FROM campaigns c JOIN channel_promotion_bindings b
      ON b.proxy_node_id=c.proxy_node_id AND b.channel_id=c.channel_id
      JOIN sponsor_channels ch ON ch.id=c.channel_id
      WHERE c.proxy_node_id=%s AND (c.placement_status='scheduled' OR (c.placement_status='active' AND EXISTS(SELECT 1 FROM promotion_runtime r WHERE r.campaign_id=c.id AND r.state='applied')))
      AND c.moderation_status='approved' AND c.payment_status='verified'
      AND c.archived_at IS NULL AND ch.archived_at IS NULL
      AND b.channel_url=ch.telegram_url AND b.node_host=%s AND b.node_port=%s
      AND c.scheduled_start_at<=now() AND c.scheduled_end_at>now()
      ORDER BY c.scheduled_start_at,c.id LIMIT 2""", (node['id'],host,port)).fetchall()
    if len(rows) != 1:
        return result
    c=rows[0]
    return dict(result,state='apply',campaign_id=str(c['id']),tag=c['ad_tag'],expires_at=c['scheduled_end_at'].timestamp())


def revision(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()


def handle(conn, request):
    plan=desired(conn,request['host'],int(request['port']))
    if request.get('action')=='ack':
        if revision(plan)!=request.get('revision'):
            return {'accepted':False}
        if plan.get('node_id'):
            conn.execute("""INSERT INTO promotion_runtime(proxy_node_id,campaign_id,state)
              VALUES(%s,%s,%s) ON CONFLICT(proxy_node_id) DO UPDATE SET
              campaign_id=EXCLUDED.campaign_id,state=EXCLUDED.state,checked_at=now()""",
              (plan['node_id'],plan.get('campaign_id'),'applied' if plan['state']=='apply' else 'idle'))
        return {'accepted':True}
    return dict(plan,revision=revision(plan))


def main():
    # Только stdin/stdout контейнера, без HTTP и публикации тегов в журнале.
    request=json.loads(sys.stdin.read(8192))
    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=3,row_factory=dict_row,
                         options='-c statement_timeout=5000') as conn:
        result=handle(conn,request)
    print(json.dumps(result))

if __name__=='__main__':
    try:
        main()
    except Exception:
        print('{"error":"promotion_runtime_unavailable"}')
        sys.exit(1)
