from pathlib import Path
from html.parser import HTMLParser
import re,json,subprocess,sys,datetime,shutil
class Audit(HTMLParser):
 def __init__(self):super().__init__();self.ids=[];self.fields=[];self.labels=[];self.stack=[];self.lang=False;self.status=False;self.alert=False;self.main=False
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id' in a:self.ids.append(a['id'])
  if tag=='html':self.lang=bool(a.get('lang'))
  if tag=='label' and a.get('for'):self.labels.append(a['for'])
  if tag in ('input','textarea','select'):self.fields.append((a,'label' in self.stack))
  if tag=='main':self.main=True
  if a.get('role')=='status':self.status=True
  if a.get('role')=='alert':self.alert=True
  if tag not in ('input','meta','link','br','img','hr'):self.stack.append(tag)
 def handle_endtag(self,tag):
  if tag in self.stack:self.stack=self.stack[:len(self.stack)-1-self.stack[::-1].index(tag)]
def lum(h):
 rgb=[int(h[i:i+2],16)/255 for i in (1,3,5)]
 rgb=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
 return sum(a*b for a,b in zip(rgb,[.2126,.7152,.0722]))

folder=Path(__file__).resolve().parent
summary=[]
p=Audit();html=(folder/'static/index.html').read_text();p.feed(html)
css=(folder/'static/style.css').read_text()
checks={'html_language':p.lang,'main_landmark':p.main,'unique_static_ids':len(p.ids)==len(set(p.ids)),
'static_fields_have_labels':all(a.get('aria-label') or wrapped or a.get('id') in p.labels for a,wrapped in p.fields),
'live_status_and_error':p.status and p.alert,'visible_focus_css':':focus-visible' in css,'skip_link':'class="skip"' in html,'responsive_breakpoint':'@media(max-width:' in css}
contrasts=[{'foreground':a,'background':b,'ratio':round((max(lum(a),lum(b))+.05)/(min(lum(a),lum(b))+.05),2)} for a,b in [('#193d38', '#f4f1e8'), ('#ffffff', '#204f43'), ('#496158', '#fffdf7')]]
checks['sampled_text_contrast_4_5']=all(c['ratio']>=4.5 for c in contrasts)
audit={'scope':'Static source audit only; not browser, screen-reader, zoom, mobile or WCAG certification. Dynamic labels reviewed in source, not executed DOM.',
'checks':checks,'contrast_samples':contrasts,'all_checked_pass':all(checks.values())}
(folder/'evidence/static-accessibility-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=folder,capture_output=True,text=True)
output=result.stdout+result.stderr
(folder/'evidence/test-run-final.txt').write_text(output)
node=subprocess.run(['node','--check','static/app.js'],cwd=folder,capture_output=True,text=True)
web_client=subprocess.run([sys.executable,'tests/check_web_client.py'],cwd=folder,capture_output=True,text=True,timeout=30)
(folder/'evidence/web-client-check.txt').write_text(web_client.stdout+web_client.stderr)
sdk=subprocess.run([sys.executable,'tests/check_official_sdk.py'],cwd=folder,capture_output=True,text=True,timeout=40)
(folder/'evidence/official-sdk-check.txt').write_text(sdk.stdout+sdk.stderr)
compile_result=subprocess.run([sys.executable,'-m','compileall','-q','.'],cwd=folder,capture_output=True,text=True)
count=re.search(r'Ran (\d+) tests',output)
summary.append({'folder':folder.name,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'python':sys.version.split()[0],'tests':int(count.group(1)) if count else 0,'tests_pass':result.returncode==0,'javascript_syntax_pass':node.returncode==0,'web_client_checks_pass':web_client.returncode==0,'official_sdk_status':'PASS' if sdk.returncode==0 else 'BLOCKED' if sdk.returncode==77 else 'FAIL','python_compile_pass':compile_result.returncode==0,'static_audit_pass':audit['all_checked_pass']})
if result.returncode:print(output)
if web_client.returncode:print(web_client.stdout+web_client.stderr)
if not audit['all_checked_pass']:print(folder.name,checks)
(folder/'evidence/verification-summary.json').write_text(json.dumps(summary[-1],indent=2)+'\n')
print(json.dumps(summary[0],indent=2))
if sdk.returncode not in (0,77) or not all(summary[0][k] for k in ("tests_pass","javascript_syntax_pass","web_client_checks_pass","python_compile_pass","static_audit_pass")):sys.exit(1)
