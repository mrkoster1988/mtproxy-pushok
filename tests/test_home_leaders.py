"""Home leaderboard carousel regression checks."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "api" / "web"


class HomeLeadersTests(unittest.TestCase):
    def test_scroll_is_contained(self):
        css = (WEB / "interface.css").read_text(encoding="utf-8")
        self.assertIn(".home-leader-avatars{", css)
        self.assertIn("overflow-x:auto;overflow-y:hidden", css)
        self.assertIn("flex:0 0 44px", css)
        self.assertIn("scroll-snap-type:x proximity", css)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_leaders_behavior(self):
        source = (WEB / "community.js").read_text(encoding="utf-8")
        harness = r"""
const document={addEventListener(){}},errors={},setInterval=()=>{},setTimeout=()=>{};
const assert=(ok,msg)=>{if(!ok)throw Error(msg);};
const el=(tag,text,cls)=>({tag,text,cls,children:[],dataset:{},attributes:{},hidden:false,isConnected:true,
 append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},
 setAttribute(k,v){this.attributes[k]=v;}});
let token='session-a',rows=[],fail=false;
const api=async url=>{assert(url==='/hall?kind=month&metric=invited','ranking endpoint');if(fail)throw Error('temporary');return {rows};};
const window={pushokViews:{userAvatar:(id,name)=>{const n=el('span',name);n.id=id;return n;}},pushokHall:{frame:(node,rank)=>{node.frame=rank;}}};
SOURCE
return (async()=>{
 const target=el('section'),leaders=window.pushokCommunity.leaders;
 const track=()=>target.children[1],items=()=>track().children;
 await leaders(target);assert(items().length===1&&items()[0].cls==='muted','empty state');
 rows=[{user_id:1,rank:1,first_name:'A'}];await leaders(target);assert(items().length===1&&items()[0].children[0].frame===1,'one leader');
 rows=Array.from({length:12},(_,i)=>({user_id:i+1,rank:i+1,first_name:'U'+(i+1)})).reverse();
 await leaders(target);assert(items().length===10,'top ten');assert(items().every((item,i)=>item.children[0].frame===i+1&&item.children[0].dataset.hallRank===String(i+1)&&item.children[1].text==='#'+(i+1)),'rank order, frames and labels');
 assert(target.children[0].children[1].href==='#/hall','full leaderboard link');
 const firstTrack=track();await leaders(target);assert(track()===firstTrack,'same data preserves scroll');
 fail=true;await leaders(target);assert(target.hidden,'failure state');fail=false;await leaders(target);assert(!target.hidden&&track()===firstTrack,'retry restores same list');
 token='session-b';await leaders(target);assert(track()!==firstTrack,'new session redraws');
 assert(target.attributes['aria-busy']==='false','busy cleared');
 return 'Behavior checks passed';
})();"""
        body = harness.replace("SOURCE", source, 1)
        subprocess.run(
            ["node", "-e", "new Function(" + json.dumps(body) + ")().catch(e=>{console.error(e);process.exitCode=1;})"],
            check=True, capture_output=True, text=True, timeout=15,
        )
