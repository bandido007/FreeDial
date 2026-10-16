/* Deterministic demo-centre transports for the unchanged production page. */
(() => {
  const now=Date.now()/1000;
  const test=window.__supervisorTest={commands:[],sockets:0,
    data:{user:'daudi',name:'Daudi',can:['monitor','whisper','barge','force'],canManage:true,notReadyReasons:['Break','Meeting'],reasonLimits:{Break:900},longCallSeconds:600,
      teams:[{uid:'claims',name:'Claims & Providers',agents:['zawadi','peter','halima','mussa'],queues:['Claims','Providers','Escalations']},{uid:'front',name:'Front Desk',agents:['juma'],queues:['Membership','General']}],
      agents:[{uid:'halima',name:'Halima',number:'7105',availability:'ready',activity:'idle',stateSince:now-123,queues:['Claims','Providers']},{uid:'mussa',name:'Mussa',number:'7108',availability:'ready',activity:'wrapup',stateSince:now-42,queues:['Providers','Escalations']},{uid:'peter',name:'Peter',number:'7106',availability:'ready',activity:'talking',call:'peter-call',stateSince:now-712,queues:['Claims','Providers']},{uid:'zawadi',name:'Zawadi',number:'7107',availability:'not_ready',activity:'idle',reason:'Break',stateSince:now-1450,queues:['Claims','Escalations']},{uid:'juma',name:'Juma',number:'7101',availability:'ready',activity:'idle',stateSince:now-30,queues:['Membership','General']}],
      queues:[{name:'Claims',offered:100,serviceLevelPercent:84,targetPercent:80,targetSeconds:30},{name:'Providers',offered:20,serviceLevelPercent:90,targetPercent:90,targetSeconds:20},{name:'Escalations',offered:10,serviceLevelPercent:80,targetPercent:80,targetSeconds:60},{name:'Membership',offered:0,targetPercent:80,targetSeconds:30},{name:'General',offered:0,targetPercent:70,targetSeconds:60}],
      calls:[{call:'peter-call',queue:'Claims',caller:'0712',language:'English',state:'connected',agent:'peter',connected_at:now-712},...Array.from({length:5},(_,i)=>({call:`waiting-${i}`,queue:i<3?'Claims':i===3?'Providers':'Escalations',state:'waiting',caller:'0700',waiting_since:now-(i<3?100:i===3?12:110)}))]},
    event(type,data){this.socket.onmessage({data:JSON.stringify({type:'event',event:{type,data,at:new Date().toISOString()}})});}
  };
  const original=fetch,reply=data=>({json:async()=>data});
  window.fetch=async(url,options={})=>{
    if(String(url).includes('access_token'))return reply({success:true,access_token:'test',refresh_token:'refresh',expires_in:3600});
    if(String(url).includes('revoke_token'))return reply({});
    if(!String(url).includes('graphql'))return original(url,options);
    const {query,variables:v={}}=JSON.parse(options.body);let data={commandId:'command'},dataList=[];
    if(query.includes('mySupervisorDesk'))data=structuredClone(test.data);
    else if(query.includes('myTeamMeasures')) {test.measured=v;data={until:new Date().toISOString(),
      team:{answered:v.period==='today'?12:80,missed:2,medianAnswerSeconds:6.4,wrapups:12,medianWrapupSeconds:41,corrections:1,transfers:3,transfersCompleted:2,summariesShown:5,summariesWrong:1,overLimit:2,actedOn:1,endedByAgent:1,medianMinutesToAction:3.5},
      agents:[{uid:'halima',number:'7105',name:'Halima',answered:7,missed:0,medianAnswerSeconds:5.1,medianWrapupSeconds:38,transfers:1,transfersCompleted:1},{uid:'peter',number:'7106',name:'Peter',answered:5,missed:2,medianAnswerSeconds:9.8,medianWrapupSeconds:55,transfers:2,transfersCompleted:1}]};}
    else if(query.includes('supervisedAgentHistory')) {data=null;dataList=[{at:new Date().toISOString(),type:'agent.state',description:'not ready / idle: Break'}];}
    else if(query.includes('startSupervisorSession'))data={sipId:'websup1',password:'test-only',domain:'desk.test',wsUrl:'wss://desk.test/sip'};
    else {test.commands.push({query,variables:v});
      if(query.includes('superviseCall'))test.event('supervise.started',{supervisor:'daudi',call:v.call,mode:v.mode});
      if(query.includes('stopSupervising'))test.event('supervise.stopped',{supervisor:'daudi'});
      if(query.includes('forceAgentState')) {const agent=test.data.agents.find(a=>a.uid===v.agent);agent.availability=v.state;agent.reason=v.reason;test.event('agent.state',{agent:v.agent,availability:v.state,activity:'idle',reason:v.reason});}
    }
    return reply({data:{answer:{status:true,code:9001,data,dataList}}});
  };
  window.WebSocket=class{constructor(){test.sockets++;test.socket=this;queueMicrotask(()=>this.onopen?.());}send(){this.onmessage({data:JSON.stringify({type:'hello',desk:test.data})});}close(){}};
  window.JsSIP={debug:{disable(){}},WebSocketInterface:class{},UA:class{constructor(){this.handlers={};}on(name,fn){this.handlers[name]=fn;}start(){queueMicrotask(()=>this.handlers.registered());}stop(){}}};
})();
