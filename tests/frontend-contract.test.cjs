const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync('static/app.js','utf8');
const fixtures={
 '/api/conversations':[{id:4,user_name:'Ana',origin:'panel',mood:'caliente',profile:{objetivo:'Python'}}],
 '/api/conversations/4':{id:4,user_name:'Ana',origin:'panel',mood:'caliente',profile:{objetivo:'Python'},telegram_chat_id:-42,messages:[{id:9,role:'assistant',content:'Hola'}]},
 '/api/metrics':{conversations:1,with_ai:1,with_human:0,moods:{caliente:1},courses:18},
 '/api/catalog':[{title:'Python',hours:32,prices:{estudiante_uta:40},contents:'Variables',url:'https://example.test'}],
 '/api/health':{telegram:true,llm:true,model:'gpt-4.1-mini'},
 '/api/simulate':{id:4}
};
const calls=[];
const ctx=vm.createContext({console,document:{},setTimeout,clearTimeout,fetch:async(path,options={})=>{calls.push({path,options});return {ok:true,json:async()=>fixtures[path]||{ok:true}}}});
vm.runInContext(source.slice(0,source.indexOf('function switchView')),ctx);
(async()=>{
 const state=await vm.runInContext("api('/api/state')",ctx);
 assert.equal(state.chats[0].id,'4');assert.equal(state.chats[0].channel,'demo');assert.equal(state.chats[0].status,'lead caliente');assert.equal(state.metrics.hot,1);
 const detail=await vm.runInContext("api('/api/chats/4')",ctx);assert.equal(detail.messages[0].delivery,'demo');
 const courses=await vm.runInContext("api('/api/courses')",ctx);assert.match(courses[0].prices[0],/40/);assert.equal(courses[0].content,'Variables');
 const health=await vm.runInContext("api('/api/health')",ctx);assert.equal(health.telegram,'configured');assert.equal(health.llm,'configured');
 await vm.runInContext("post('/api/demo')",ctx);assert.ok(JSON.parse(calls.at(-1).options.body).telegram_chat_id<0);
 await vm.runInContext("post('/api/demo/4',{text:'Hola'})",ctx);assert.equal(JSON.parse(calls.at(-1).options.body).telegram_chat_id,-42);
 await vm.runInContext("post('/api/chats/4/mode',{mode:'human'})",ctx);assert.equal(calls.at(-1).path,'/api/conversations/4/mode');
 console.log('Frontend contract: 7 scenarios passed');
})().catch(e=>{console.error(e);process.exitCode=1});
