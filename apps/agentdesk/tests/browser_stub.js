/* Test-only transport and media fixtures. Production app.js is loaded unchanged. */
(() => {
  const base = { availability: 'not_ready', activity: 'idle', reason: 'Break', call: null, wrapupCall: null, endpoint: 'PJSIP/web7001' };
  const test = window.__deskTest = {
    state: {...base}, commands: [], sockets: 0, muted: false,
    call: {call:'call-1',caller:'+255 712 000 123',queue:'Claims',language:'Swahili',summary:'Member asks why their claim was rejected. Has their member number ready.',ringTimeout:15,wrapupSeconds:60,waitSeconds:38},
    data() { return {uid:this.uid || 'juma',number:'7001',name:this.name || 'Juma',notReadyReasons:['Break','Lunch'],wrapupCodes:['Information given','Follow-up required'],state:{...this.state},stateSince:new Date().toISOString(),queues:[{name:'Claims',waiting:3,longestWait:100,ringTimeout:15,wrapupSeconds:60},{name:'Membership',waiting:0,longestWait:0},{name:'General',waiting:1,longestWait:20}],myDay:{calls:23,averageTalkSeconds:221,notReadySeconds:1080},currentCall:this.state.call || this.state.wrapupCall ? this.call : null,transferTargets:{queues:['Claims'],agents:[{uid:'neema',number:'7002',name:'Neema',availability:'ready',activity:'idle'}]}}; },
    event(type,data) { this.socket?.onmessage?.({data:JSON.stringify({type:'event',event:{type,data,at:new Date().toISOString()}})}); },
    change(data) { this.state={...this.state,...data}; this.event('agent.state',this.state); },
    offer() { this.call.connectedAt=null; this.call.offeredAt=Date.now()/1000; this.event('call.offered',this.call); this.change({activity:'offered',call:'call-1',wrapupCall:null}); this.session=new Session(); this.ua.emit('newRTCSession',{session:this.session,originator:'remote'}); },
    incomingBeforeOffer() { this.session = new Session(); this.session.remote_identity.uri.user = '+255 713 000 456'; this.ua.emit('newRTCSession',{session:this.session,originator:'remote'}); },
    ready() { this.change({availability:'ready',activity:'idle',reason:'',call:null,wrapupCall:null}); },
    wrap() { this.session?.terminate(); },
  };
  const reply = (data) => ({json:async()=>data});
  const original = window.fetch;
  window.fetch = async (url, options={}) => {
    if (String(url).includes('access_token')) return reply({success:true,access_token:'test-token',refresh_token:'test-refresh',expires_in:3600});
    if (String(url).includes('revoke_token')) return reply({});
    if (!String(url).includes('graphql')) return original(url,options);
    const {query,variables:v={}}=JSON.parse(options.body);
    let data={commandId:'test-command'};
    if (query.includes('myAgentDesk')) data=test.data();
    else {
      test.commands.push({query,variables:v});
      if (query.includes('startAgentSession')) {data={sipId:'web7001',password:'test-only',wsUrl:'wss://desk.test/sip',domain:'desk.test'};}
      else if(query.includes('setMyAvailability')) {const ready=query.includes('ready: true');test.change({availability:ready?'ready':'not_ready',reason:ready?'':v.reason || 'Phone not connected'});}
      else if(query.includes('holdMyCall')) test.change({activity:query.includes('hold: true')?'on_hold':'talking'});
      else if(query.includes('finishMyWrapup')) {test.event('agent.wrapped_up',{agent:'juma',call:'call-1',by:'agent',code:v.code,notes:v.notes});test.change({availability:v.ready?'ready':'not_ready',activity:'idle',reason:v.reason || '',call:null,wrapupCall:null});}
      else if(query.includes('transferMyCall')) {test.event('transfer.started',{agent:'juma',transfer:'t1',kind:v.kind,target:v.target});test.change({activity:'on_hold'});test.event('transfer.answered',{transfer:'t1'});}
      else if(query.includes('myTransferStep') && v.step==='cancel') {test.event('transfer.cancelled',{transfer:'t1'});test.change({activity:'talking'});}
    }
    return reply({data:{answer:{status:true,code:9001,message:'OK',data}}});
  };
  window.WebSocket=class {
    constructor() {test.sockets++;test.socket=this;queueMicrotask(()=>this.onopen?.());}
    send() {this.onmessage?.({data:JSON.stringify({type:'hello',state:test.state})});}
    close() {this.onclose?.({code:1000});}
  };
  class Emitter {constructor(){this.handlers={};}on(name,fn){(this.handlers[name] ||= []).push(fn);}emit(name,data={}){for(const fn of this.handlers[name] || [])fn(data);}}
  class Session extends Emitter {
    constructor(){super();this.established=false;this.ended=false;this.remote_identity={uri:{user:'+255 712 000 123'}};}
    isEstablished(){return this.established;}isEnded(){return this.ended;}isMuted(){return {audio:test.muted};}
    answer(){this.established=true;test.call.connectedAt=Date.now()/1000;test.change({activity:'talking'});test.event('call.connected',{call:'call-1'});this.emit('confirmed');}
    terminate(){this.ended=true;this.established=false;this.end_time=new Date();this.emit('ended');test.change({activity:'wrapup',call:null,wrapupCall:'call-1'});test.event('call.ended',{call:'call-1',result:'answered',reason:'hangup'});}
    mute(){test.muted=true;}unmute(){test.muted=false;}sendDTMF(value){test.digits=value;}
  }
  class UA extends Emitter {constructor(){super();test.ua=this;}start(){queueMicrotask(()=>this.emit('registered'));}stop(){this.emit('unregistered');}}
  window.JsSIP={UA,WebSocketInterface:class{},debug:{disable(){}}};
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}});
})();
