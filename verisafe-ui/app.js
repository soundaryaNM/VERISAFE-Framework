// Minimal interactivity for VERISAFE UI demo
const state = {
  analysis: 'Not Started',
  planning: 'Not Started',
  generation: 'Not Started',
  verification: 'Not Started',
  execution: 'Not Started',
  testsGenerated: 0,
  approved: 0,
};

function updateUI(){
  document.querySelectorAll('.step').forEach(el=>{
    const step = el.dataset.step;
    el.classList.remove('running','done','blocked');
    const sts = state[step];
    el.querySelector('.status').textContent = sts;
    if(sts==='Running') el.classList.add('running');
    if(sts==='Done') el.classList.add('done');
    if(sts==='Blocked') el.classList.add('blocked');
  });

  // Enable buttons only when prior done
  document.getElementById('planScenarios').disabled = state.analysis !== 'Done';
  document.getElementById('generateTests').disabled = state.planning !== 'Done';
  document.getElementById('requestApproval').disabled = state.generation !== 'Done';
  document.getElementById('execute').disabled = state.verification !== 'Done' || state.approved===0;

  document.getElementById('testsCount').textContent = state.testsGenerated;
  document.getElementById('approvedCount').textContent = state.approved;
  document.getElementById('genCountRight').textContent = state.testsGenerated;
  document.getElementById('appCountRight').textContent = state.approved;
}

function setStageRunning(stage){
  state[stage] = 'Running';
  updateUI();
  document.getElementById('completeStage').disabled = false;
}

function completeStage(stage){
  state[stage] = 'Done';
  // side-effects
  if(stage==='generation'){
    state.testsGenerated = Math.max(5, state.testsGenerated+5);
  }
  if(stage==='verification'){
    // remain blocked until approval
  }
  updateUI();
  document.getElementById('completeStage').disabled = true;
}

document.getElementById('runAnalysis').addEventListener('click', ()=>{
  setStageRunning('analysis');
});
document.getElementById('planScenarios').addEventListener('click', ()=>{
  setStageRunning('planning');
});
document.getElementById('generateTests').addEventListener('click', ()=>{
  setStageRunning('generation');
});
document.getElementById('requestApproval').addEventListener('click', ()=>{
  // open a tiny approval modal? for demo, mark verification blocked until approved
  alert('Approval requested. In a real run this requires human approval.');
});
document.getElementById('execute').addEventListener('click', ()=>{
  setStageRunning('execution');
});

document.getElementById('completeStage').addEventListener('click', ()=>{
  // find the currently running stage
  const running = Object.keys(state).find(k=>state[k]==='Running');
  if(running) completeStage(running);
  // After generation completes, enable verification button (requires approval)
  if(running==='generation'){
    state.verification = 'Awaiting Input';
    document.getElementById('requestApproval').disabled = false;
  }
  updateUI();
});

// Bottom pane controls
const bottom = document.getElementById('bottomPane');
document.getElementById('togglePane').addEventListener('click', ()=>{
  const hidden = bottom.getAttribute('aria-hidden') === 'true';
  bottom.setAttribute('aria-hidden', hidden ? 'false' : 'true');
  document.getElementById('togglePane').textContent = hidden ? 'Collapse' : 'Expand';
});

// Tab switching
document.querySelectorAll('.pane-header .tabs button').forEach(b=>{
  b.addEventListener('click', ()=>{
    document.querySelectorAll('.pane-header .tabs button').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    const tab = b.dataset.tab;
    document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('hidden', t.dataset.tab!==tab));
  });
});

// Step click shows detail
document.querySelectorAll('.step').forEach(el=>{
  el.addEventListener('click', ()=>{
    const s = el.dataset.step;
    document.getElementById('stepTitle').textContent = s.charAt(0).toUpperCase()+s.slice(1);
    document.getElementById('stepDesc').textContent = 'Status: '+state[s]+'. Actions available below.';
    // select appropriate action
    updateUI();
  });
});

// initialize
updateUI();
