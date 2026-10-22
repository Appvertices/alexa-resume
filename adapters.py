"""Small local intent model; not Alexa speech recognition or a hosted LLM."""
import math
import re
from collections import Counter
PHRASES = {
 "complete":["done","finished","all done","that is complete","I finished this step","mark it done","I'm done","I did it","finished it"],
 "pause":["pause","hold on","I need a break","stop for now","pause the routine","stop","take a break","wait"],
 "resume":["resume","continue","I am back","where was I","let us continue","I'm back","let's continue","keep going","carry on","go on"],
 "status":["status","what is next","what remains","show my progress","next step","next","what's next","what now"],
 "undo":["undo","undo that","go back","that was a mistake","reverse last step","undo please"],
 "defer":["skip","later","do this later","defer this step","move this to the end"],
 "reset":["reset","start over","restart","clear my progress"],
}
def tokens(text): return re.findall(r"[a-z]+",text.lower())
class LocalIntentModel:
    label="Local intent classifier + MCP tools; no Alexa device or external model"
    def __init__(self):
        self.counts={k:Counter(w for s in rows for w in tokens(s)) for k,rows in PHRASES.items()}
        self.vocab=set(w for c in self.counts.values() for w in c)
    def predict(self,text):
        text=text.strip().lower().replace("\u2019", "'")
        # Negation and override phrases are outside this tiny model's safe grammar.
        if re.search(r"\b(not|never|no|ignore|override|cannot|can't|cant|won't|wont|if|will|would|could|might|almost)\b|\b\w+n't\b", text):
            return "clarify",0.0
        words=tokens(text)
        # Exact phrase match ignores punctuation, so "where was I?" matches "where was I".
        for intent,phrases in PHRASES.items():
            if words in [tokens(p) for p in phrases]: return intent,1.0
        if not words or not any(w in self.vocab for w in words): return "clarify",0.0
        scores={}
        for intent,c in self.counts.items():
            scores[intent]=sum(math.log((c[w]+1)/(sum(c.values())+len(self.vocab))) for w in words)
        peak=max(scores.values());weights={k:math.exp(v-peak) for k,v in scores.items()}
        best=max(weights,key=weights.get);confidence=weights[best]/sum(weights.values())
        return (best if confidence >= .60 else "clarify"),round(confidence,4)

