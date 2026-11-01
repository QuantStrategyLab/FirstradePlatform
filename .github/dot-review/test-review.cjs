'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const r = require('./review.cjs');
const policy = require('./policy.json');
const sha = 'a'.repeat(40), base = 'b'.repeat(40);
const file = {filename: 'application/orders.py', status: 'modified', sha,
  additions: 1, deletions: 1, patch: '@@ -1 +1 @@\n-old\n+new'};

test('trusted classifier rejects missing patches and protects renamed old paths', () => {
  assert.throws(() => r.classify([{...file, patch: ''}], 1, policy));
  assert.equal(r.classify([{...file, filename:'README.md', previous_filename:file.filename,
    status:'renamed'}], 1, policy).tier, 'R2');
  assert.equal(r.classify([{...file,filename:'README.md'}], 1, policy).tier, 'R0');
  assert.equal(r.classify([{...file,filename:'ordinary.py'}], 1, policy).tier, 'R1');
});
test('posthoc is the exact merged commit and single parent, never original head', () => {
  const pr = {merged:true, state:'closed', merge_commit_sha:sha, head:{sha:'c'.repeat(40)}};
  assert.equal(r.targetBinding(pr,'posthoc',sha,base,{sha,parents:[{sha:base}]}).base,base);
  assert.throws(() => r.targetBinding(pr,'posthoc',pr.head.sha,base,{sha,parents:[{sha:base}]}));
  assert.throws(() => r.targetBinding(pr,'posthoc',sha,base,{sha,parents:[{sha:base},{sha}]}));
  assert.throws(() => r.targetBinding(pr,'posthoc',sha,sha,{sha,parents:[{sha:base}]}));
});
test('premerge requires current open non-draft head and base', () => {
  const pr={state:'open',draft:false,head:{sha},base:{sha:base}};
  assert.equal(r.targetBinding(pr,'premerge',sha,base).base,base);
  for(const changed of [{state:'closed'},{draft:true},{head:{sha:base}}])
    assert.throws(() => r.targetBinding({...pr,...changed},'premerge',sha,base));
});
test('frozen intent is deterministic and all material changes change the key', () => {
  const request={mode:'agent',materials:[{binding:{base_sha:base,head_sha:sha}}]};
  const a=r.intent('example/repo',12,7,request);
  assert.deepEqual(a,r.intent('example/repo',12,7,JSON.parse(JSON.stringify(request))));
  assert.notEqual(a.key,r.intent('example/repo',12,7,{...request,model:'different'}).key);
  assert.match(a.key,/^dot-review:v1:12:7:[a-f0-9]{64}$/);
});
test('ambiguous POST only looks up the original key and never posts twice', async () => {
  let posts=0,lookups=0;
  const i=r.intent('example/repo',12,7,{mode:'agent'});
  const api=async(method,path)=>{if(method==='POST'){posts++;throw Error('lost');}
    lookups++;assert.equal(path,'/v1/tasks/lookup');return {id:'task-one'};};
  assert.equal((await r.submitOnce(api,i,false)).id,'task-one');
  assert.equal(posts,1);assert.equal(lookups,1);
  await r.submitOnce(api,i,true);assert.equal(posts,1);assert.equal(lookups,2);
  await assert.rejects(r.submitOnce(async()=>{throw Error('missing');},i,true));
});
test('completed result must bind real attempt, revision, source and complete review', () => {
  const binding={repository:'example/repo',target_sha:sha};
  const request={materials:[{binding}],output_schema:r.outputSchema(binding,1)};
  const i=r.intent('example/repo',12,7,request);
  const output={binding,ai_review:{provider:'dot',model:'actual-model',run_ref:'https://example.test/run/1',mode:'independent'},
    execution_status:'completed',verdict:'pass',coverage:{complete:true,reviewed_files:1},blocking_findings:[],dispositions:[],limitations:[]};
  const task={id:'task-one',owner:'example/repo',request,provider:'dot',revision:1,attempt:'attempt-one',worker:'worker-one',status:'completed',output};
  assert.equal(r.validateResult(task,i).eligible,true);
  for(const change of [{provider:'fake'},{revision:2},{attempt:null},{owner:'other/repo'},
    {output:{...output,ai_review:{...output.ai_review,run_ref:''}}}]) assert.throws(()=>r.validateResult({...task,...change},i));
  assert.equal(r.validateResult({...task,output:{...output,limitations:['missing source']}},i).eligible,false);
  assert.equal(r.validateResult({...task,output:{...output,blocking_findings:[{severity:'P1'}]}},i).eligible,false);
  assert.equal(r.validateResult({...task,status:'running',output:null},i).eligible,false);
});

test('failed upstream gate can select one PR; wrong workflows and ambiguous associations stop', async () => {
  const run={workflow_id:12,status:'completed',conclusion:'failure',event:'pull_request_target',
    repository:{full_name:'example/repo'},pull_requests:[{number:7}]};
  const github={rest:{actions:{getWorkflowRun:async()=>({data:run}),
    getWorkflow:async()=>({data:{id:12,path:'.github/workflows/risk-gate.yml',name:'PR risk gate'}})},
    pulls:{list:async()=>({data:[]})}}};
  const context={repo:{owner:'example',repo:'repo'},eventName:'workflow_run',payload:{workflow_run:{id:5}}};
  assert.equal(await r.select(github,context),7);
  run.workflow_id=13;await assert.rejects(r.select(github,context));run.workflow_id=12;
  run.pull_requests=[{number:7},{number:8}];await assert.rejects(r.select(github,context));
  run.pull_requests=[];await assert.rejects(r.select(github,context));
  run.event='schedule';assert.equal(await r.select(github,context),null);
});

for (const kind of ['premerge','posthoc']) test(kind + ': offline submit/collect keeps one task and never grants approval', async () => {
  const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
  const cwd=process.cwd(), directory=fs.mkdtempSync(path.join(os.tmpdir(),'dot-review-test-'));
  const env={...process.env}, originalFetch=global.fetch;
  const comments=[], calls=[], outputs={};let task=null, posts=0;
  const content='synthetic source\n', sourceSHA='d'.repeat(40);
  const pr={number:7,changed_files:1,commits:1,title:'Synthetic change',body:'Synthetic body',
    state:'open',draft:false,head:{sha},base:{sha:base,ref:'main',repo:{id:12}}};
  const methods={files:()=>[file],reviews:()=>[],reviewComments:()=>[],comments:()=>comments,commits:()=>[{}]};
  const github={rest:{checks:{listForRef:()=>[]},repos:{listCommitStatusesForRef:()=>[],get:async()=>({data:{id:12,full_name:'example/repo',default_branch:'main'}}),
    getBranch:async()=>({data:{commit:{sha:base}}}),
    getCommit:async()=>({data:{sha,parents:[{sha:base}],files:[file]},headers:{}})},pulls:{get:async()=>({data:pr}),
    listFiles:methods.files,listReviews:methods.reviews,listReviewComments:methods.reviewComments,listCommits:methods.commits},
    issues:{listComments:methods.comments,
      createComment:async args=>{const c={id:comments.length+1,body:args.body,user:{id:41898282,type:'Bot',login:'github-actions[bot]'},
        issue_url:'https://api.github.com/repos/example/repo/issues/7'};comments.push(c);return {data:c};},
      updateComment:async args=>{const c=comments.find(c=>c.id===args.comment_id);c.body=args.body;return {data:c};},
      getComment:async args=>({data:comments.find(c=>c.id===args.comment_id)})},
    git:{getTree:async()=>({data:{truncated:false,tree:[{path:file.filename,type:'blob',mode:'100644',sha:sourceSHA,size:content.length}]}}),
      getBlob:async()=>({data:{sha:sourceSHA,encoding:'base64',content:Buffer.from(content).toString('base64'),size:content.length}})}},
    paginate:async fn=>JSON.parse(JSON.stringify(fn())),
    graphql:async()=>({repository:{pullRequest:{reviewThreads:{nodes:[],pageInfo:{hasNextPage:false,endCursor:null}}}}})};
  const summary={addRaw(){return this;},async write(){}};
  const core={setOutput:(k,v)=>outputs[k]=v,getIDToken:async audience=>{
    assert.equal(audience,'https://audience.example.test');return 'synthetic-only';},summary};
  const context={repo:{owner:'example',repo:'repo'},ref:'refs/heads/main',eventName:'workflow_dispatch',runId:1,
    serverUrl:'https://github.com',payload:{inputs:{pr_number:'7',review_kind:kind,target_sha:sha,expected_base_sha:base}}};
  try {
    if(kind === 'posthoc') Object.assign(pr,{state:'closed',merged:true,merge_commit_sha:sha,head:{sha:'c'.repeat(40)}});
    process.chdir(directory);
    Object.assign(process.env,{TRUSTED_SHA:base,DOT_REVIEW_READY:'true',DOT_REVIEW_PROFILE:'review',DOT_REVIEW_MODEL:'fixture-model',
      AI_SERVICE_URL:'https://tasks.example.test',AI_SERVICE_AUDIENCE:'https://audience.example.test'});
    global.fetch=async(url,options)=>{
      calls.push([options.method,url]);
      if(options.method==='POST') {posts++;const request=JSON.parse(options.body);
        task={id:'synthetic-task',owner:'example/repo',provider:'dot',revision:1,status:'running',request,output:null,artifacts:[]};
        return Response.json(task,{status:202});}
      if(url.endsWith('/lookup')) return Response.json({id:task.id});
      return Response.json(task);
    };
    await r.prepare({github,context,core});assert.equal(outputs.prepared,'true');
    await r.submit({github,context,core});assert.equal(posts,1);assert.equal(comments.length,1);
    context.runId=2;
    await r.prepare({github,context,core});await r.submit({github,context,core});assert.equal(posts,1);
    task={...task,status:'completed',attempt:'attempt-one',worker:'worker-one',output:{binding:task.request.materials[0].binding,
      ai_review:{provider:'dot',model:'fixture-model',run_ref:'https://example.test/actual/1',mode:'independent'},
      execution_status:'completed',verdict:'pass',coverage:{complete:true,reviewed_files:1},blocking_findings:[],dispositions:[],limitations:[]}};
    context.payload.inputs={locator_comment_id:'1',task_id:task.id};
    const before=calls.length;
    await r.collect({github,context,core});await r.collect({github,context,core});
    assert.equal(comments.length,2);assert.equal(posts,1);
    assert.ok(calls.slice(before).every(([method])=>method==='GET'));
    const result=JSON.parse(fs.readFileSync('dot-review/result.json'));
    assert.equal(result.technical_result_valid,true);assert.equal(result.approval,false);
    assert.equal(result.publication,'bot-draft-only');assert.equal(result.comment_id,2);
    comments.push({id:3,body:'A new unresolved concern',user:{id:99,type:'User',login:'reviewer'}});
    await r.collect({github,context,core});
    assert.equal(JSON.parse(fs.readFileSync('dot-review/result.json')).stale,true);
  } finally {
    global.fetch=originalFetch;process.chdir(cwd);
    for(const key of Object.keys(process.env)) if(!(key in env)) delete process.env[key];
    Object.assign(process.env,env);fs.rmSync(directory,{recursive:true,force:true});
  }
});
