import copy
from adapters import LocalIntentModel
PROTOCOL_VERSION = "2025-11-25"


def rpc_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def message_kind(message):
    """Validate the JSON-RPC envelope before dispatch; batch messages are unsupported."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise ValueError("JSON-RPC 2.0 object required.")
    has_id = "id" in message
    if has_id and type(message["id"]) not in (str, int):
        # Error responses can have a null ID when the request ID was unreadable.
        if not (message["id"] is None and "error" in message and "method" not in message):
            raise ValueError("Invalid request ID.")
    if "method" in message:
        if not isinstance(message["method"], str) or "result" in message or "error" in message:
            raise ValueError("Invalid request envelope.")
        return "request" if has_id else "notification"
    if not has_id or ("result" in message) == ("error" in message) or "params" in message:
        raise ValueError("Invalid response envelope.")
    if "error" in message:
        error = message["error"]
        if (not isinstance(error, dict) or type(error.get("code")) is not int
                or not isinstance(error.get("message"), str)):
            raise ValueError("Invalid error response.")
    elif not isinstance(message["result"], dict):
        raise ValueError("MCP result must be an object.")
    return "response"

ROUTINES={
 "departure":{"name":"Out the door","steps":["Put your keys in your bag","Check your wallet","Pack the item you need today","Take your water bottle"]},
 "desk":{"name":"Start a focused session","steps":["Choose one task for this session","Open only what that task needs","Put distractions aside","Write your first small action"]},
 "close":{"name":"Close the workday","steps":["Save your current work","Write the next step for tomorrow","Tidy one small area","Close your work tools"]},
}
TOOLS=[
 {"name":"routine_state","description":"Read current routine and progress. Local only.","inputSchema":{"type":"object","properties":{},"additionalProperties":False},"annotations":{"readOnlyHint":True}},
 {"name":"routine_start","description":"Start one bundled routine; reset requires confirmation when progress exists.","inputSchema":{"type":"object","properties":{"routine_id":{"type":"string","enum":list(ROUTINES)},"version":{"type":"integer"},"confirmed":{"type":"boolean"}},"required":["routine_id","version"],"additionalProperties":False}},
 {"name":"routine_turn","description":"Interpret a local typed turn. Checklist changes are reversible; no external actions.","inputSchema":{"type":"object","properties":{"text":{"type":"string","maxLength":400},"version":{"type":"integer"}},"required":["text","version"],"additionalProperties":False}},
 {"name":"routine_restore","description":"Restore a user-supplied local checkpoint after explicit confirmation.","inputSchema":{"type":"object","properties":{"checkpoint":{"type":"object"},"version":{"type":"integer"},"confirmed":{"type":"boolean"}},"required":["checkpoint","version","confirmed"],"additionalProperties":False}}
]
class Engine:
    def __init__(self):
        self.version=0;self.routine_id="departure";self.steps=["pending"]*4
        self.paused=False;self.history=[];self.pending_reset=False
        self.message="Ready when you are. Put your keys in your bag."
        self.model=LocalIntentModel();self.trace=[];self.initialized=False;self.negotiated=False
    def current(self): return next((i for i,v in enumerate(self.steps) if v=="pending"),None)
    def summary(self):
        if self.paused:return "Paused. Your progress is here when you return."
        i=self.current()
        if i is None:
            deferred=[j for j,v in enumerate(self.steps) if v=="deferred"]
            if deferred:return f"{len(deferred)} step(s) set aside. Say resume to return to them."
            return "Your routine is complete. You can undo the last step."
        return f"Next: {ROUTINES[self.routine_id]['steps'][i]}."
    def snapshot(self):
        return {"version":self.version,"routine_id":self.routine_id,"routines":ROUTINES,
                "steps":self.steps[:],"current":self.current(),"paused":self.paused,
                "message":self.message,"pending_reset":self.pending_reset,
                "completed":self.steps.count("done"),"trace":copy.deepcopy(self.trace[-12:]),
                "integration":self.model.label,
                "checkpoint":{"format":"resume-checkpoint-v1","routine_id":self.routine_id,"steps":self.steps[:],"paused":self.paused}}
    def save(self):
        self.history.append((self.routine_id,self.steps[:],self.paused));self.history=self.history[-30:]
    def handle(self,action,data):
        if type(data.get("version")) is not int or data.get("version")!=self.version:raise ValueError("Progress changed. Refresh before another action.")
        if action=="restore":
            c=data.get("checkpoint")
            if data.get("confirmed") is not True:raise ValueError("Confirm replacement before restoring a checkpoint.")
            if not isinstance(c,dict) or set(c)!={"format","routine_id","steps","paused"}:raise ValueError("Invalid checkpoint fields.")
            if c["format"]!="resume-checkpoint-v1" or c["routine_id"] not in ROUTINES:raise ValueError("Unsupported checkpoint format or routine.")
            if not isinstance(c["steps"],list) or len(c["steps"])!=4 or any(s not in ("pending","done","deferred") for s in c["steps"]) or type(c["paused"]) is not bool:raise ValueError("Invalid checkpoint progress.")
            self.save();self.routine_id=c["routine_id"];self.steps=c["steps"][:];self.paused=c["paused"];self.pending_reset=False
            self.message="Checkpoint restored. "+self.summary()
        elif action=="start":
            routine=data.get("routine_id")
            if routine not in ROUTINES:raise ValueError("Choose a listed routine.")
            if any(s!="pending" for s in self.steps) and data.get("confirmed") is not True:raise ValueError("Confirm replacement of your existing progress.")
            self.save();self.routine_id=routine;self.steps=["pending"]*4;self.paused=False;self.pending_reset=False;self.message=self.summary()
        elif action=="turn":
            turn=data.get("text")
            if not isinstance(turn,str) or not turn.strip() or len(turn)>400:raise ValueError("Use a short instruction (1–400 characters).")
            if turn.strip().lower()=="confirm reset" and self.pending_reset:
                self.save();self.steps=["pending"]*4;self.paused=False;self.pending_reset=False
                intent,confidence="confirmed reset",1.0;self.message=self.summary()
            else:
                intent,confidence=self.model.predict(turn)
                if intent!="reset":self.pending_reset=False
                if intent=="complete":
                    i=self.current()
                    if self.paused:self.message="You are paused. Say resume before completing a step."
                    elif i is None:self.message=self.summary()
                    else:self.save();self.steps[i]="done";self.message=self.summary()
                # Pause/resume save an undo point only when they change state, so undo never consumes a no-op.
                elif intent=="pause":
                    if not self.paused:self.save();self.paused=True
                    self.message=self.summary()
                elif intent=="resume":
                    if self.paused or (self.current() is None and "deferred" in self.steps):self.save()
                    self.paused=False
                    if self.current() is None:self.steps=["pending" if s=="deferred" else s for s in self.steps]
                    self.message=f"You completed {self.steps.count('done')} of 4. "+self.summary()
                elif intent=="undo":
                    if self.history:self.routine_id,self.steps,self.paused=self.history.pop();self.message="Undone. "+self.summary()
                    else:self.message="Nothing to undo yet. "+self.summary()
                elif intent=="defer":
                    i=self.current()
                    if self.paused:self.message="Resume first, then set a step aside."
                    elif i is None:self.message=self.summary()
                    else:self.save();self.steps[i]="deferred";self.message="Set aside, not completed. "+self.summary()
                elif intent=="reset":self.pending_reset=True;self.message="Reset this routine? Choose Confirm reset or keep your progress."
                elif intent=="status":self.message=self.summary()
                else:self.message="I am not sure. Choose Done, Pause, Resume, Set aside, or Undo. Nothing changed."
            self.trace.append({"intent":intent,"confidence":confidence,"tool":"routine_turn","external_calls":0})
        else:raise ValueError("Unknown action.")
        self.version+=1
        return {"message":self.message}
    def rpc(self,request):
        try:
            kind=message_kind(request)
        except ValueError:
            return 400,rpc_error(None,-32600,"Invalid JSON-RPC message.")
        rid=request.get("id")
        def error(code,message):return 200,rpc_error(rid,code,message)
        # This server sends no requests. Unknown responses are accepted and ignored.
        if kind=="response":return 202,None
        method=request.get("method")
        # Never execute request-only methods or reply to a notification.
        if kind=="notification":
            if not isinstance(request.get("params",{}),dict):return 400,None
            if method=="notifications/initialized" and self.negotiated:self.initialized=True
            return 202,None
        if not isinstance(request.get("params",{}),dict):return error(-32602,"Parameters must be an object.")
        if method=="ping":return 200,{"jsonrpc":"2.0","id":rid,"result":{}}
        if method=="initialize":
            params=request.get("params",{})
            info=params.get("clientInfo")
            if (not isinstance(params.get("protocolVersion"),str) or not params["protocolVersion"]
                    or not isinstance(params.get("capabilities"),dict) or not isinstance(info,dict)
                    or not isinstance(info.get("name"),str) or not isinstance(info.get("version"),str)):
                return error(-32602,"protocolVersion, capabilities and clientInfo are required.")
            if self.negotiated:return error(-32600,"Session already initialized.")
            self.negotiated=True
            # Return our supported version; the client decides whether it supports it.
            return 200,{"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":PROTOCOL_VERSION,"capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"resume-local","version":"0.1.1"}}}
        if not self.initialized:return error(-32002,"Initialize first.")
        if method=="tools/list":result={"tools":TOOLS}
        elif method=="tools/call":
            params=request.get("params",{});name=params.get("name");args=params.get("arguments",{})
            if not isinstance(args,dict):return error(-32602,"Arguments must be an object.")
            specification=next((t for t in TOOLS if t['name']==name),None)
            if specification is None:return error(-32602,"Unknown tool.")
            schema=specification['inputSchema']
            if set(args)-set(schema['properties']) or any(k not in args for k in schema.get('required',[])):
                return error(-32602,"Arguments do not match the tool schema.")
            types={"integer":int,"string":str,"boolean":bool,"object":dict}
            if any(type(value) is not types[schema['properties'][key]['type']] for key,value in args.items()):
                return error(-32602,"Argument types do not match the tool schema.")
            try:
                if name=="routine_state":pass
                elif name=="routine_start":self.handle("start",args)
                elif name=="routine_turn":self.handle("turn",args)
                elif name=="routine_restore":self.handle("restore",args)
                else:return error(-32602,"Unknown tool.")
                import json
                result={"content":[{"type":"text","text":json.dumps(self.snapshot())}],"isError":False}
            except (ValueError,KeyError,TypeError) as exc:result={"content":[{"type":"text","text":str(exc)}],"isError":True}
        else:return error(-32601,"Method not found.")
        return 200,{"jsonrpc":"2.0","id":rid,"result":result}

