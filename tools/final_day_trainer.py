#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, datetime as dt, json, os, subprocess, sys, time
from pathlib import Path
from typing import Any, Dict, List
ROOT=Path(__file__).resolve().parents[1]
MEMORY=ROOT/'memory'/'studio'; LOGS=ROOT/'logs'/'studio'; CONFIG=ROOT/'config'; DOCS=ROOT/'docs'
STATE=MEMORY/'current_state.json'; AGENTS=MEMORY/'agent_logs.jsonl'; TRAIN=LOGS/'final_day_training.jsonl'; BUDGET=MEMORY/'final_day_submission_budget.json'
NOTES=DOCS/'Notes.md'; HANDOVER=DOCS/'Handover.md'
sys.path.insert(0,str(ROOT/'tools'))
try:
    from deepseek_client import chat_json, env_config, parse_json_object
except Exception:
    chat_json=None
    def env_config(): return {'api_key':'','base_url':'https://api.deepseek.com','model':'DeepSeek-V4-pro'}
    def parse_json_object(x): return json.loads(x)

def now(): return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def clk(): return dt.datetime.now().strftime('%H:%M:%S')
def read_json(p:Path,d:Any):
    try: return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d
    except Exception: return d
def write_json(p:Path,d:Any):
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
def append_jsonl(p:Path,o:Dict[str,Any]):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a',encoding='utf-8') as f: f.write(json.dumps(o,ensure_ascii=False)+'\n')
def event(agent,typ,msg,extra=None):
    ev={'time':clk(),'iso':now(),'agent':agent,'type':typ,'message':msg}
    if extra is not None: ev['extra']=extra
    append_jsonl(AGENTS,ev)
    st=read_json(STATE,{})
    st.setdefault('events',[]).append(ev); st['events']=st['events'][-200:]
    write_json(STATE,st)
    return ev
def run(cmd,timeout=300):
    try:
        p=subprocess.run(cmd,cwd=str(ROOT),capture_output=True,text=True,timeout=timeout)
        return {'ok':p.returncode==0,'code':p.returncode,'stdout':p.stdout[-12000:],'stderr':p.stderr[-12000:],'cmd':cmd}
    except Exception as e:
        return {'ok':False,'code':-1,'stdout':'','stderr':f'{type(e).__name__}: {e}','cmd':cmd}
def parse_json_output(text):
    t=(text or '').strip()
    if not t: return {}
    for line in reversed(t.splitlines()):
        if line.startswith('JSON_RESULT='):
            try: return json.loads(line.split('=',1)[1])
            except Exception: return {}
    try: return json.loads(t)
    except Exception: return {}
def case_files():
    files=[]
    for p in (ROOT/'generated_cases').glob('*/*.txt'): files.append(p)
    base=ROOT/'cases'/'large_seed301.txt'
    if base.exists(): files.insert(0,base)
    seen=[]; out=[]
    for p in files:
        r=p.resolve()
        if r not in seen:
            out.append(p); seen.append(r)
    pref=['tiny','small','medium_seed201','medium_seed202','medium_seed203','high_noise','large_seed301','large_seed302','scarce','low']
    return sorted(out,key=lambda p:(next((i for i,k in enumerate(pref) if k in str(p)),99),str(p)))
def evaluate(solver='submission/solver.py',limit=12,timeout_case=140):
    out=[]
    for p in case_files()[:limit]:
        r=run([sys.executable,'local_test.py',solver,str(p),'--json'],timeout=timeout_case)
        obj=parse_json_output(r.get('stdout',''))
        out.append({'case':p.stem,'path':str(p.relative_to(ROOT)),'ok':bool(obj.get('ok',r['ok'])),'valid':bool(obj.get('valid',obj.get('ok',r['ok']))),'score':obj.get('total_score',obj.get('score')),'covered_tasks':obj.get('covered_tasks'),'total_tasks':obj.get('total_tasks'),'assignments':obj.get('assignments'),'couriers_used':obj.get('couriers_used'),'avg_backups_per_bundle':obj.get('avg_backups_per_bundle'),'time_sec':obj.get('time_sec'),'raw_score_sum':obj.get('raw_score_sum'),'errors':obj.get('errors',[]),'warnings':obj.get('warnings',[]),'stderr':r.get('stderr','')[-1200:]})
    return out
def summarize(results):
    scores=[x['score'] for x in results if isinstance(x.get('score'),(int,float))]
    valid=sum(1 for x in results if x.get('ok') and x.get('valid'))
    slow=[x for x in results if (x.get('time_sec') or 0)>9.65]
    bad=[x for x in results if not (x.get('ok') and x.get('valid'))]
    return {'case_count':len(results),'valid_count':valid,'avg_score':round(sum(scores)/len(scores),6) if scores else None,'max_score':max(scores) if scores else None,'slow_cases':[x['case'] for x in slow],'bad_cases':[x['case'] for x in bad]}
def deepseek_plan(round_no,summary,results,change):
    cfg=env_config(); prompt={
        'task':'final_day_offline_training_attribution_and_iteration_plan',
        'deadline':'2026-06-07 24:00 Asia/Singapore',
        'remaining_online_submissions':read_json(BUDGET,{}).get('remaining',18),
        'round':round_no,'change':change,'summary':summary,'results':results[:12],
        'rules':['lower_is_better','do_not_auto_submit','solver.py no comments and <100KB','Qwen OCR only','DeepSeek-V4-pro LLM only','protect small/tiny/scarce','prefer safe config-only patch','target solve runtime 9.0-9.6s, hard stop under 10s']
    }
    if not cfg.get('api_key') or chat_json is None:
        return {'source':'local_fallback','summary':'DeepSeek key missing; use local no-regression loop only.','next_focus':summary.get('slow_cases') or summary.get('bad_cases') or ['large_seed301'],'patch_objective':'保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。','risk_guard':['small/tiny/scarce regression rejects patch']}
    messages=[{'role':'system','content':'输出严格 JSON，字段为 summary,next_focus,patch_objective,risk_guard,submit_recommendation。不要 markdown。'}, {'role':'user','content':json.dumps(prompt,ensure_ascii=False)}]
    res=chat_json(messages,max_tokens=3072,timeout=90)
    append_jsonl(MEMORY/'chat.jsonl',{'time':now(),'role':'assistant','model':res.model,'source':'final_day_trainer_deepseek','ok':res.ok,'message':res.content or res.error})
    if not res.ok:
        return {'source':'deepseek_error','summary':res.error,'next_focus':summary.get('slow_cases') or ['large_seed301'],'patch_objective':'DeepSeek failed; run conservative local patch gate.','risk_guard':['fallback only']}
    try:
        obj=parse_json_object(res.content); obj['source']='deepseek'; return obj
    except Exception as e:
        return {'source':'deepseek_parse_error','summary':str(e),'next_focus':['large_seed301'],'patch_objective':'DeepSeek JSON parse failed; no aggressive change.','risk_guard':['fallback only']}
def patch_once(objective,timeout=520):
    return run([sys.executable,'tools/autonomous_patch_agent.py','--source','final_day_trainer','--objective',objective,'--no-pre-backup'],timeout=timeout)
def write_md(round_no,summary,plan,results,patch_res):
    DOCS.mkdir(exist_ok=True)
    block='\n\n## Final-day training round %s · %s\n\n- Summary: `%s`\n- DeepSeek plan: `%s`\n- Patch objective: `%s`\n- Patch ok: `%s`\n\n| case | score | valid | time | backups |\n|---|---:|---|---:|---:|\n%s\n' % (round_no,now(),json.dumps(summary,ensure_ascii=False),json.dumps(plan,ensure_ascii=False)[:1000],plan.get('patch_objective',''),patch_res.get('ok'), '\n'.join('| %s | %s | %s | %s | %s |' % (r.get('case'),r.get('score'),r.get('valid'),r.get('time_sec'),r.get('avg_backups_per_bundle')) for r in results[:12]))
    for p,title in [(NOTES,'# Notes.md\n'),(HANDOVER,'# Handover.md\n')]:
        if not p.exists(): p.write_text(title,encoding='utf-8')
        p.write_text(p.read_text(encoding='utf-8',errors='ignore')+block,encoding='utf-8')
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--source',default='cli'); ap.add_argument('--rounds',type=int,default=3); ap.add_argument('--base',default='cases/large_seed301.txt'); ap.add_argument('--seed',type=int,default=301); ap.add_argument('--submission-budget',type=int,default=18); ap.add_argument('--change',default='final day efficient offline training'); ap.add_argument('--no-patch',action='store_true'); ap.add_argument('--case-limit',type=int,default=12)
    args=ap.parse_args(); MEMORY.mkdir(exist_ok=True); LOGS.mkdir(exist_ok=True); CONFIG.mkdir(exist_ok=True)
    budget=read_json(BUDGET,{'remaining':args.submission_budget,'used':0,'updated_at':now(),'note':'manual online submissions only'})
    budget['remaining']=min(int(budget.get('remaining',args.submission_budget)),args.submission_budget); budget['updated_at']=now(); write_json(BUDGET,budget)
    event('Leader','final-day-start',f'剩余线上提交预算按 {budget["remaining"]} 次管理；本脚本只离线训练，不自动提交。')
    gen=run([sys.executable,'tools/generate_midtrain_cases.py','--base',args.base,'--target','all','--seed',str(args.seed)],timeout=260)
    event('Data Seed Agent','generate-cases','基于 large_seed301 生成多场景离线训练样本。',{'ok':gen['ok'],'stdout':gen['stdout'][-1200:],'stderr':gen['stderr'][-1200:]})
    all_reports=[]
    for i in range(1,args.rounds+1):
        event('Trainer','round-start',f'Final-day round {i}: local evaluation -> DeepSeek attribution -> gated self-iteration')
        results=evaluate(limit=args.case_limit)
        summary=summarize(results)
        plan=deepseek_plan(i,summary,results,args.change)
        objective=plan.get('patch_objective') or args.change
        patch_res={'ok':None,'skipped':True,'reason':'--no-patch'}
        if not args.no_patch:
            patch_res=patch_once(objective)
        after=evaluate(limit=args.case_limit) if (not args.no_patch) else results
        after_summary=summarize(after)
        report={'time':now(),'round':i,'source':args.source,'before':summary,'deepseek_plan':plan,'patch':patch_res,'after':after_summary,'after_cases':after}
        append_jsonl(TRAIN,report); all_reports.append(report)
        event('Evaluator','round-result',f'Round {i} valid {after_summary["valid_count"]}/{after_summary["case_count"]}, avg={after_summary.get("avg_score")}',after_summary)
        write_md(i,after_summary,plan,after,patch_res)
    final={'ok':True,'reports':all_reports,'budget':read_json(BUDGET,budget),'generated_cases':read_json(MEMORY/'generated_cases_latest.json',{})}
    print(json.dumps(final,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
