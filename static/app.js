"use strict";
const $ = s => document.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const id = () => crypto.randomUUID();
const statuses = {draft:"構想・未着手",designing:"設計中",reviewing:"確認中",approved:"確定",archived:"保管"};
const kinds = {inlet:"吸気系",compressor:"圧縮機",combustor:"燃焼器",turbine:"タービン",exhaust:"排気系",shaft:"ロータ・軸",bearing:"軸受",casing:"ケーシング",heat_exchanger:"熱交換器",auxiliary:"補機",other:"その他"};
const tabs = {overview:"概要",components:"部品構成",parameters:"仕様・設計値",stations:"評価断面",results:"設計成果物",files:"資料",notes:"設計メモ",history:"変更履歴",compare:"設計案の比較"};
const stationGroups = {
  engine: {label:"ガスタービン全体", prefix:"GT"}, compressor: {label:"圧縮機設計", prefix:"CP"},
  turbine: {label:"タービン設計", prefix:"TB"}, combustor: {label:"燃焼器設計", prefix:"CB"}
};
let stationGroup = "engine";
let projects = [], current = null, activeTab = "overview", componentId = "", search = "", filter = "active", comparison = null, formCallback = null, formBusy = false, formDirty = false, listGeneration = 0;
const date = v => v ? new Date(v).toLocaleString("ja-JP",{year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—";
const badge = s => `<span class="badge ${esc(s)}">${esc(statuses[s] || s)}</span>`;
const emptyValue = v => v ? esc(v) : '<span class="missing">未設定</span>';
const button = (action,label,extra="",cls="") => `<button type="button" class="button ${cls}" data-action="${action}" ${extra}>${label}</button>`;
function toast(message,error=false){const t=$("#toast");t.textContent=message;t.className="visible"+(error?" error":"");clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.className="",5500);}
async function api(path,data){const options=data===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-Design-Token":$('meta[name="design-token"]').content},body:JSON.stringify(data)};const response=await fetch(path,options);const result=await response.json();if(!response.ok)throw new Error(result.error||"処理できませんでした。");return result;}
async function refresh(){projects=await api("/api/projects");}
function p(){return current.project;}
function selected(){return p().components.find(c=>c.id===componentId);}
function componentName(cid){return p().components.find(c=>c.id===cid)?.name || "案件全体";}
function nav(){
  $("#breadcrumb").innerHTML=current?`<button data-action="home">ワークスペース</button><span>/</span>${esc(p().name)}`:"ワークスペース <span>/</span> 設計プロジェクト";
  $("#project-nav").innerHTML=current?`<div class="nav-label">${esc(p().code || "PROJECT")} · 部品構成</div><button class="tree-button ${componentId?"":"active"}" data-action="select-component" data-id=""><span class="tree-dot"></span>案件全体</button>${p().components.map(c=>`<button class="tree-button ${componentId===c.id?"active":""}" data-action="select-component" data-id="${c.id}"><span class="tree-dot"></span><span class="tree-name">${esc(c.name)}</span></button>`).join("")}`:"";
}
function heading(eyebrow,title,subtitle,actions){return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${esc(title)}</h1><p>${subtitle}</p></div><div class="heading-actions">${actions}</div></div>`;}
function stat(label,value,suffix=""){return `<div class="stat"><div class="stat-label">${label}</div><div class="stat-value">${value}<small>${suffix}</small></div></div>`;}
function empty(title,description,action=""){return `<div class="empty"><div class="empty-icon">◇</div><h3>${title}</h3><p>${description}</p>${action}</div>`;}
function home(){current=null;componentId="";comparison=null;nav();renderHome();}
function renderHome(){
  const active=projects.filter(x=>x.status!=="archived");
  $("#content").innerHTML=heading("ENGINEERING WORKSPACE","設計プロジェクト","構想から3D設計まで、設計の根拠と情報をひとつの場所に。",button("import","↓ JSONを取込")+button("new-project","＋ 新規プロジェクト","","primary"))+
    `<div class="stats">${stat("プロジェクト",active.length,"件")}${stat("設計中",active.filter(x=>x.status==="designing").length,"件")}${stat("確認中",active.filter(x=>x.status==="reviewing").length,"件")}${stat("登録部品",active.reduce((s,x)=>s+x.component_count,0),"点")}</div><div class="toolbar"><div class="search-wrap"><input id="project-search" aria-label="プロジェクトを検索" placeholder="案件名・番号・担当者で検索" value="${esc(search)}"></div><select id="status-filter" aria-label="状態で絞り込み"><option value="active">進行中の案件</option><option value="all">すべての案件</option>${Object.entries(statuses).map(([k,v])=>`<option value="${k}">${v}</option>`).join("")}</select></div><div id="project-list" class="project-grid"></div><p class="subtle lead-note">設計情報の登録・整理・比較を行うワークスペースです。計算モジュールは今後追加する構成です。</p>`;
  $("#status-filter").value=filter;renderCards();
}
function renderCards(){const items=projects.filter(x=>(filter==="all"||filter==="active"&&x.status!=="archived"||x.status===filter)&&[x.name,x.code,x.owner,x.architecture,x.application].join(" ").toLowerCase().includes(search.toLowerCase()));
  $("#project-list").innerHTML=items.length?items.map(x=>`<article class="project-card"><div class="card-top"><div class="project-symbol">✳</div>${badge(x.status)}</div><div class="project-code">${esc(x.code || "NO PROJECT CODE")}</div><button class="project-title" data-action="open" data-id="${x.id}">${esc(x.name)}</button><div class="project-subtitle">${esc([x.application,x.architecture].filter(Boolean).join(" / ") || "用途・構成は未設定")}</div><div class="card-meta"><span>◇ 部品 ${x.component_count}</span><span>≡ 仕様 ${x.requirement_count}</span></div><div class="card-footer"><span>${esc(x.owner || "担当未設定")}</span><span>更新 ${date(x.updated_at)}</span></div></article>`).join(""):`<div class="wide-empty">${empty(projects.length?"該当する案件がありません":"最初の設計プロジェクトを作成","案件の基本情報と部品構成から始めましょう。<br>一軸ガスタービンの構成テンプレートも使えます。",button("new-project","＋ プロジェクトを作成","","primary"))}</div>`;
}
async function openProject(projectId){const env=await api("/api/project?id="+encodeURIComponent(projectId));current=env;activeTab="overview";stationGroup="engine";componentId="";comparison=null;render();}
function render(){if(!current)return renderHome();nav();
  $("#content").innerHTML=heading(esc(p().code || "DESIGN PROJECT"),p().name,`${badge(p().status)} <span class="revision">Rev. ${current.revision} · 最終保存 ${date(current.updated_at)}</span>`,button("edit-project","基本情報を編集")+button("clone","設計案を複製")+`<a class="button" href="/api/export?id=${p().id}" download>↓ JSONを書出し</a>`)+`<div class="tabs" role="tablist" aria-label="設計情報">${Object.entries(tabs).map(([k,v])=>`<button class="tab ${activeTab===k?"active":""}" role="tab" aria-selected="${activeTab===k}" data-action="tab" data-tab="${k}">${v}</button>`).join("")}</div><section id="tab-content"></section>`;
  renderTab();
}
function scopeSelect(){return `<div class="selected-component"><label for="component-scope">対象</label><select id="component-scope"><option value="">案件全体</option>${p().components.map(c=>`<option value="${c.id}" ${componentId===c.id?"selected":""}>${esc(c.name)}</option>`).join("")}</select></div>`;}
function panel(title,body,actions=""){return `<div class="panel"><div class="panel-head"><h3>${title}</h3><div class="heading-actions">${actions}</div></div>${body}</div>`;}

function renderStations(target) {
  const stations = p().stations || [];
  const layout = p().station_layout;
  const groups = Object.entries(stationGroups).map(([key, {label}]) => {
    const count = stations.filter(item => item.group === key).length;
    return `<option value="${key}" ${key === stationGroup ? "selected" : ""}>${label}（${count}）</option>`;
  }).join("");
  const visible = stations.filter(item => item.group === stationGroup)
    .sort((a, b) => a.sequence - b.sequence || a.code.localeCompare(b.code));
  const engine = stationGroup === "engine";
  const rows = visible.map(item => {
    const reference = stations.find(s => s.id === item.engine_station_id);
    const normal = item.cycle_station_id;
    const cooled = item.cooled_cycle_station_id ?? normal;
    const cycleMapping = normal === cooled ? normal : [normal && `${normal}（冷却なし）`, cooled && `${cooled}（冷却あり）`].filter(Boolean).join(" / ");
    const mapping = engine ? cycleMapping : reference?.code;
    return `<tr><td><span class="value">${esc(item.code)}</span><div>${esc(item.name)}</div><div class="subtle">${item.sequence}${item.component_id ? ` · ${esc(componentName(item.component_id))}` : ""}</div></td>
      <td>${emptyValue(item.position)}</td><td>${emptyValue(item.quantities)}${item.basis ? `<div class="subtle">${esc(item.basis)}</div>` : ""}</td>
      <td>${emptyValue(mapping)}</td><td><div class="row-actions">
      ${button("edit-station", "編集", `data-id="${item.id}"`, "small")}
      <button class="text-button danger" data-action="remove-station" data-id="${item.id}">削除</button></div></td></tr>`;
  }).join("");
  const body = rows ? `<div class="table-wrap"><table><thead><tr><th>断面・流れ順</th><th>位置の定義</th><th>評価量・集計条件</th>
    <th>${engine ? "Cycle Studio位置ID" : "全機側の対応"}</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : empty("評価断面が未登録です", "段数と出口構成を設定するか、断面を個別に追加してください。");
  const configuration = layout ? `遠心圧縮機 ${layout.compressor_stages}段 · 圧縮機出口ガイドベーン${layout.axial_guide_vanes ? "あり" : "なし"} · 軸流タービン ${layout.turbine_stages}段 · 排気ディフューザあり` : "段構成未設定";
  target.innerHTML = `<div class="toolbar"><div class="selected-component"><label for="station-group">断面区分</label>
    <select id="station-group">${groups}</select></div>${button("station-preset", "段数・構成を設定", "", "small")}</div>
    <div class="hint"><strong>${configuration}</strong><br>全機側は GasTurb 15 の発電用番号です。Cycle Studio の位置IDを別に表示します。部品側で抽気・混合・接続ダクトをまたぐ場合は、両端の位置と集計条件を記録してください。</div>` +
    panel(stationGroups[stationGroup].label, body, button("new-station", "＋ 断面を追加", "", "small primary"));
  $("#station-group").onchange = event => { stationGroup = event.target.value; renderStations(target); };
}

function stationForm(stationId) {
  const items = p().stations || [];
  const existing = items.find(item => item.id === stationId);
  const group = existing?.group || stationGroup;
  const {label, prefix} = stationGroups[group];
  const item = existing || {id:id(), group, code:`${prefix}-`, sequence:Math.min(10000,
    Math.max(0, ...items.filter(s => s.group === group).map(s => s.sequence)) + 10)};
  const parts = p().components.filter(part => part.kind === group);
  const engine = group === "engine";
  const mappings = engine ? {"":"対応なし", "0":"0", "1":"1", "2":"2", "3":"3", "4":"4", "5":"5"}
    : {"":"対応なし", ...Object.fromEntries(items.filter(s => s.group === "engine").map(s => [s.id, `${s.code} ${s.name}`]))};
  const fields = `<div class="form-grid">${field("code", "断面ID", item.code, {required:true, max:40})}` +
    `${field("sequence", "流れ順", item.sequence, {required:true, max:5})}</div>` +
    field("name", "断面名", item.name, {required:true, max:150}) +
    (engine ? "" : field("component_id", "対象部品", item.component_id ?? (parts.length === 1 ? parts[0].id : ""),
      {select:{"":"未指定", ...Object.fromEntries(parts.map(part => [part.id, part.name]))}})) +
    (engine ? `<div class="form-grid">${field("cycle_station_id", "Cycle Studio位置ID（冷却なし）", item.cycle_station_id || "", {select:mappings})}` +
      `${field("cooled_cycle_station_id", "Cycle Studio位置ID（冷却あり）", item.cooled_cycle_station_id ?? item.cycle_station_id ?? "", {select:mappings})}</div>`
      : field("engine_station_id", "全機側の対応断面", item.engine_station_id || "", {select:mappings})) +
    field("position", "位置の定義", item.position, {max:1000, placeholder:"例　静翼後縁の下流5 mm、図面断面 A–A"}) +
    field("quantities", "評価量", item.quantities, {max:500}) +
    field("basis", "集計・座標の条件", item.basis, {area:true, max:1000, placeholder:"例　絶対系・質量流量平均・全周換算"});
  modal(`${label}の評価断面`, fields, async data => {
    const draft = structuredClone(p());
    const updated = {...item, ...data, sequence:Number(data.sequence)};
    draft.stations = existing ? items.map(s => s.id === stationId ? updated : s) : [...items, updated];
    await saveDraft(draft);
  });
}

function stationPresetForm() {
  const layout = p().station_layout || {compressor_stages:2, turbine_stages:3, axial_guide_vanes:true};
  const stageInput = (name, label) => `<label class="field"><span>${label}</span>
    <input name="${name}" type="number" min="1" max="24" step="1" required value="${layout[name]}"></label>`;
  const fields = `<div class="form-grid">${stageInput("compressor_stages", "遠心圧縮機の段数")}
    ${stageInput("turbine_stages", "軸流タービンの段数")}</div>` +
    field("axial_guide_vanes", "圧縮機出口の90°転向・軸方向ガイドベーン", layout.axial_guide_vanes ? "yes" : "no",
      {select:{yes:"あり", no:"なし"}}) +
    '<p class="subtle">タービンは各段に静翼・動翼を1列ずつ置き、最終段の下流に排気ディフューザを設定します。構成外になるひな形の断面は変更履歴で参照できます。</p>';
  modal("評価断面の段数と構成", fields, async data => {
    const revision = current.revision;
    const settings = {compressor_stages:Number(data.compressor_stages), turbine_stages:Number(data.turbine_stages),
                      axial_guide_vanes:data.axial_guide_vanes === "yes"};
    current = await api("/api/station-layout", {id:p().id, revision, layout:settings});
    await refresh();
    render();
    toast(current.revision === revision ? "この構成は設定済みです" : "評価断面を設定しました");
  }, "構成を適用する");
}

async function removeStation(stationId) {
  const draft = structuredClone(p());
  if (draft.stations.some(item => item.engine_station_id === stationId)) {
    throw new Error("部品側に対応する断面があります。対応を解除してから削除してください。");
  }
  if (!await confirmAction("評価断面を削除します。変更履歴から復元できます。続けますか？")) return;
  draft.stations = draft.stations.filter(item => item.id !== stationId);
  await saveDraft(draft);
}

const performanceLabels = {equal:"均等配分案", "75-percent":"75%配分案"};
const sourceKinds = {cfd:"CFD", test:"試験", synthetic:"仮想"};

function performanceDetails(item, projectId, base) {
  const point = item.conditions;
  const value = (x, scale, places) => x == null ? "—" : (x * scale).toFixed(places);
  return `<div class="subtle">${point.inlet_total_temperature_k} K · ${value(point.inlet_total_pressure_pa,.001,3)} kPa（絶対圧） ·
    ${point.air_mass_flow_kg_s} kg/s · ${point.shaft_speed_rpm.toLocaleString("ja-JP")} rpm</div>
    <details><summary>両案の翼形状・出典・再計算結果</summary>
    ${Object.entries(performanceLabels).map(([key,label])=>{
      const design = item.designs[key];
      const shape = `/api/result-file?project=${projectId}&id=${design.blade_result_id}&part=`;
      return `<p><strong>${label}</strong>${design.replaced_by_blade_id ? ' <span class="badge reviewing">翼形状差し替え済み</span>' : ""}
        <br>${esc(sourceKinds[design.source_kind])} · ${esc(design.source_reference)}
        <br>圧縮比仕事 ${value(design.specific_work_j_kg,.001,3)} kJ/kg · 正味出力 ${value(design.net_electric_power_w,.001,3)} kW ·
        正味燃料消費率 ${value(design.specific_fuel_consumption_kg_kwh,1000,2)} g/kWh
        <br>翼形状SHA-256 <code>${esc(design.project_sha256)}</code>
        <br>再計算のサイクル実行ID ${esc(design.run_id)}
        <br><a href="${shape}primary" download>${label}の翼形状JSON</a> ·
        <a href="${shape}handoff" download>手渡し記録</a> · <a href="${base}${key}" download>サイクル結果JSON</a></p>`;
    }).join("")}
    <p class="subtle">基準サイクル実行ID ${esc(item.base_cycle_run_id)}<br>基準入力SHA-256 ${esc(item.base_cycle_input_hash)}</p></details>`;
}

function resultRow(item, projectId) {
  const isBlade = item.kind === "blade";
  const base = `/api/result-file?project=${projectId}&id=${item.id}&part=`;
  const state = item.outdated ? '<span class="badge reviewing">再評価が必要</span>' : '<span class="badge designing">現行設計版</span>';
  if (item.kind === "performance") {
    return `<tr><td><strong>${esc(item.name)}</strong>${performanceDetails(item, projectId, base)}</td>
      <td>${esc(item.component_name)}</td><td>Rev. ${item.source_revision}<div>${state}</div></td>
      <td><a href="${base}primary" download>比較一式JSON</a></td></tr>`;
  }
  const file = `<a href="${base}primary" download>${isBlade ? "翼形状JSON" : "結果JSON"}</a>`;
  const handoff = isBlade ? `<a href="${base}handoff" download>手渡し記録</a>` : "";
  const summary = [];
  if (isBlade) {
    summary.push(`${item.row_count}要素`, item.cycle_result_id ? "同じ設計版のサイクル結果と照合済み" : "対応するサイクル結果は未登録");
    if (item.stage1_share != null) summary.push(`第1段配分 ${(item.stage1_share * 100).toLocaleString("ja-JP")}%`);
    if (item.replaced_by_blade_id) summary.push("差し替え済み");
  } else {
    if (item.status === "non_generating") summary.push("発電なし");
    if (item.metrics.net_electric_power_w != null) summary.push(`正味出力 ${(item.metrics.net_electric_power_w / 1000).toFixed(3)} kW`);
    if (item.metrics.air_mass_flow_kg_s != null) summary.push(`空気流量 ${item.metrics.air_mass_flow_kg_s} kg/s`);
  }
  return `<tr><td><strong>${esc(item.name)}</strong><div class="subtle">${summary.map(esc).join(" · ")}</div>
    <details><summary>出典と識別情報</summary><div class="subtle">${isBlade ? "Blade Designer形式 " : "Cycle Studio "}${esc(isBlade ? item.schema_version : item.module_version)}
    <br>サイクル実行ID ${esc(item.run_id)}<br>入力SHA-256 ${esc(item.input_hash)}
    ${isBlade ? `<br>変換元 Cycle Studio ${esc(item.cycle_module_version)}<br>翼形状SHA-256 ${esc(item.project_sha256)}` : ""}
    <br>${item.context.project_id ? "成果物に案件情報あり" : "取込時に案件・設計版を指定"}</div></details></td>
    <td>${esc(item.component_name)}<div class="subtle">取込時の部品名</div></td>
    <td>Rev. ${item.source_revision}<div>${state}</div></td>
    <td><div class="row-actions">${file}${handoff}</div></td></tr>`;
}

function renderResults(target, generation, projectId) {
  const actions = button("import-cycle-result", "＋ サイクル結果を取込", "", "small primary") +
    button("import-blade-result", "＋ 翼形状を取込", "", "small") +
    button("import-performance-result", "＋ 性能比較を取込", "", "small");
  target.innerHTML = '<p class="subtle">設計成果物を読み込んでいます…</p>';
  api(`/api/results?id=${projectId}`).then(data => {
    if (!current || p().id !== projectId || generation !== listGeneration) return;
    const items = data.results.filter(item => !componentId || item.component_id === componentId);
    const body = items.length ? `<div class="table-wrap"><table><thead><tr><th>成果物</th><th>登録先</th><th>入力元の設計版</th><th>保存ファイル</th></tr></thead>
      <tbody>${items.map(item=>resultRow(item, projectId)).join("")}</tbody></table></div>` :
      empty("設計成果物がまだありません", "サイクル結果・翼形状・性能比較を設計版に紐付けて記録できます。");
    target.innerHTML = scopeSelect() + `<div class="hint">現行は Rev. ${data.revision}。過去の設計版や差し替え済みの翼形状に紐付く性能比較は「再評価が必要」と表示します。</div>` +
      panel("設計成果物", body, actions);
  }).catch(e => {
    if (!current || p().id !== projectId || generation !== listGeneration) return;
    target.textContent = e.message;
  });
}

function renderTab(){const target=$("#tab-content");if(!target)return;const generation=++listGeneration;
  if(activeTab==="overview"){
    const allValues=[...p().requirements,...p().components.flatMap(c=>c.parameters)];
    target.innerHTML=`<div class="stats">${stat("登録部品",p().components.length,"点")}${stat("仕様・設計値",allValues.length,"項目")}${stat("未設定の値",allValues.filter(x=>!x.value).length,"項目")}${stat("関連資料",p().links.length+p().attachments.length,"件")}</div><div class="overview-grid">${panel("プロジェクト情報",`<div class="panel-body"><dl class="details"><dt>案件番号</dt><dd>${emptyValue(p().code)}</dd><dt>用途</dt><dd>${emptyValue(p().application)}</dd><dt>エンジン構成</dt><dd>${emptyValue(p().architecture)}</dd><dt>担当</dt><dd>${emptyValue(p().owner)}</dd><dt>設計の目的</dt><dd>${emptyValue(p().purpose)}</dd></dl></div>`)}${panel("設計の確認",`<div class="panel-body"><dl class="details"><dt>未設定の値</dt><dd>${allValues.filter(x=>!x.value).length} 項目</dd><dt>出典未記入</dt><dd>${allValues.filter(x=>!x.source).length} 項目</dd><dt>確認中の部品</dt><dd>${p().components.filter(c=>c.status==="reviewing").length} 点</dd><dt>計算モジュール</dt><dd class="subtle">今後追加予定</dd></dl><p class="subtle">入力状況を表示します。数値の妥当性や設計成立性を判定するものではありません。</p></div>`)}</div>${panel("主流路の構成",`<div class="panel-body"><div class="flow-strip">${p().components.filter(c=>["inlet","compressor","combustor","turbine","exhaust","heat_exchanger"].includes(c.kind)).map(c=>`<button class="flow-node" data-action="select-component" data-id="${c.id}">${esc(c.name)}</button>`).join('<span class="flow-arrow">→</span>') || '<span class="subtle">部品構成から、流路を構成する部品を登録してください。</span>'}</div><p class="subtle">登録順に並べた構成図です。抽気・分岐や軸接続は、部品メモ・資料に記録できます。</p></div>`,button("tab","部品構成へ","data-tab="+'"components"',"small"))}`;
  } else if(activeTab==="components"){
    target.innerHTML=`<div class="toolbar"><p class="subtle">流路・ロータ・支持系など、設計対象を部品単位で整理します。</p>${button("new-component","＋ 部品を追加","","primary")}</div>`+(p().components.length?`<div class="component-list">${p().components.map(c=>`<article class="component-card"><div class="card-top"><span class="subtle">${esc(kinds[c.kind])}</span>${badge(c.status)}</div><h3><button data-action="select-component" data-id="${c.id}">${esc(c.name)}</button></h3><div class="subtle">${esc(c.stage || "段・位置 未設定")} · ${c.parameters.length} 項目</div><div class="row-actions"><button class="text-button" data-action="edit-component" data-id="${c.id}">編集</button><button class="text-button" data-action="move-component" data-id="${c.id}" data-direction="-1">↑</button><button class="text-button" data-action="move-component" data-id="${c.id}" data-direction="1">↓</button><button class="text-button danger" data-action="delete-component" data-id="${c.id}">削除</button></div></article>`).join("")}</div>`:empty("部品がまだありません","圧縮機・タービン・軸・軸受などを追加できます。"));
  } else if(activeTab==="parameters"){
    const c=selected();const values=c?c.parameters:p().requirements;
    target.innerHTML=scopeSelect()+(c?`<div class="hint"><strong>${esc(c.name)}</strong> · ${esc(kinds[c.kind])} · ${esc(c.stage || "位置未設定")}<br>${esc(c.description || "部品メモは未記入です。")}</div>`:'<p class="subtle">案件全体の要求仕様・運転条件を登録します。部品ごとの項目は対象を切り替えて管理できます。</p>')+panel(c?`${esc(c.name)}の設計値`:"要求仕様・運転条件",values.length?`<div class="table-wrap"><table><thead><tr><th>項目</th><th>値</th><th>単位</th><th>出典・根拠 / 備考</th><th>操作</th></tr></thead><tbody>${values.map(v=>`<tr><td>${esc(v.name)}</td><td class="value">${emptyValue(v.value)}</td><td>${esc(v.unit || "—")}</td><td>${emptyValue(v.source)}${v.note?`<div class="subtle">${esc(v.note)}</div>`:""}</td><td><div class="row-actions"><button class="text-button" data-action="edit-parameter" data-id="${v.id}">編集</button><button class="text-button danger" data-action="delete-parameter" data-id="${v.id}">削除</button></div></td></tr>`).join("")}</tbody></table></div>`:empty("設計項目がまだありません","値が未定でも、項目名・単位・根拠を先に登録できます。"),button("new-parameter","＋ 項目を追加","","primary small"));
  } else if(activeTab==="stations"){
    renderStations(target);
  } else if(activeTab==="results"){
    renderResults(target, generation, p().id);
  } else if(activeTab==="files"){
    const files=p().attachments.filter(x=>!componentId||x.component_id===componentId), links=p().links.filter(x=>!componentId||x.component_id===componentId);
    target.innerHTML=scopeSelect()+`<div class="hint">ファイルは選択時のコピーを案件内に保存します。元ファイルの変更には自動追従しません。1件5MB・1案件合計30MBまで。JSON書出しには現在の添付資料も含まれます。${componentId?"":" 案件全体では全部品の資料も表示します。"}</div>`+panel("添付ファイル",files.length?`<div class="table-wrap"><table><thead><tr><th>資料</th><th>関連部品</th><th>サイズ</th><th>操作</th></tr></thead><tbody>${files.map(f=>`<tr><td><div class="file-name">${esc(f.name)}</div><div class="file-note">追加 ${date(f.added_at)}</div></td><td>${esc(componentName(f.component_id))}</td><td>${(f.size/1024).toFixed(1)} KB</td><td><div class="row-actions"><a href="/api/file?project=${p().id}&id=${f.id}" download>保存</a><button class="text-button danger" data-action="delete-file" data-id="${f.id}">登録解除</button></div></td></tr>`).join("")}</tbody></table></div>`:empty("添付資料がまだありません","図面・仕様書・画像・設計データなどを保管できます。"),button("upload","＋ ファイルを追加","","primary small"))+panel("参考リンク",links.length?`<div class="table-wrap"><table><thead><tr><th>資料名 / URL</th><th>関連部品</th><th>備考</th><th>操作</th></tr></thead><tbody>${links.map(l=>`<tr><td><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.title)} ↗</a><div class="file-note">${esc(l.url)}</div></td><td>${esc(componentName(l.component_id))}</td><td>${esc(l.note)}</td><td><div class="row-actions"><button class="text-button" data-action="edit-link" data-id="${l.id}">編集</button><button class="text-button danger" data-action="delete-link" data-id="${l.id}">削除</button></div></td></tr>`).join("")}</tbody></table></div>`:empty("参考リンクがまだありません","技術資料・製品情報・出典のURLを記録できます。"),button("new-link","＋ リンクを追加","","small"));
  } else if(activeTab==="notes"){
    target.innerHTML=panel("設計判断・検討メモ",p().notes.length?p().notes.map(n=>`<article class="note-card"><div class="note-title"><h3>${esc(n.title)}</h3><span class="subtle">${date(n.created_at)}</span></div><div class="note-body">${esc(n.body)}</div><div class="row-actions"><button class="text-button" data-action="edit-note" data-id="${n.id}">編集</button><button class="text-button danger" data-action="delete-note" data-id="${n.id}">削除</button></div></article>`).join(""):empty("設計メモがまだありません","採用理由・検討事項・打合せ結果などを残せます。"),button("new-note","＋ メモを追加","","primary small"));
  } else if(activeTab==="history"){
    target.innerHTML='<p class="subtle">履歴を読み込んでいます…</p>';const pid=p().id;api("/api/history?id="+pid).then(items=>{if(!current||p().id!==pid||generation!==listGeneration)return;target.innerHTML=`<div class="hint">保存ごとに設計情報の履歴を残します。復元は新しいリビジョンとして保存するので、その後の履歴も残ります。JSON書出しの対象は現在の情報と添付資料です。</div>`+panel("変更履歴",`<div class="table-wrap"><table><thead><tr><th>リビジョン</th><th>保存日時</th><th>変更</th><th>操作</th></tr></thead><tbody>${items.map(h=>`<tr><td><span class="history-rev">Rev. ${h.revision}</span></td><td>${date(h.saved_at)}</td><td>${esc(h.action)}</td><td>${h.revision===current.revision?'<span class="subtle">現在の状態</span>':button("restore","この状態に戻す",`data-id="${h.id}" data-revision="${h.revision}"`,"small")}</td></tr>`).join("")}</tbody></table></div>`);}).catch(e=>toast(e.message,true));
  } else if(activeTab==="compare"){
    const others=projects.filter(x=>x.id!==p().id);
    target.innerHTML=`<div class="hint">案件の基本情報・要求仕様・部品情報・部品の設計値を比較します。複製した設計案はIDで対応付け、それ以外は名称で照合します。同じ名前が複数ある項目は登録順で対応付けます。資料・メモ・拡張情報は比較対象外です。</div><div class="compare-select"><label for="compare-target">比較する設計案</label><select id="compare-target"><option value="">選択してください</option>${others.map(x=>`<option value="${x.id}">${esc(x.code?x.code+" / ":"")}${esc(x.name)}</option>`).join("")}</select></div><div id="comparison-result"></div>`;
    if(comparison&&others.some(x=>x.id===comparison.project.id)){$("#compare-target").value=comparison.project.id;renderComparison();}
    else $("#comparison-result").innerHTML=empty("比較する設計案を選択",others.length?"条件や部品構成の違いを一覧で確認できます。":"「設計案を複製」すると、変更前後を比較できます。");
  }
}
function pairRows(left,right,key){const used=new Set();return [...left.map(a=>{let idx=right.findIndex((b,i)=>!used.has(i)&&b.id===a.id);if(idx<0)idx=right.findIndex((b,i)=>!used.has(i)&&key(b)===key(a));if(idx>=0)used.add(idx);return [a,idx>=0?right[idx]:null];}),...right.filter((_,i)=>!used.has(i)).map(b=>[null,b])];}
function renderComparison(){const other=comparison.project;const rows=[];const add=(label,a,b)=>rows.push({label,a:String(a??""),b:String(b??"")});
  for(const [k,label] of Object.entries({name:"案件名",code:"案件番号",application:"用途",architecture:"構成",owner:"担当",status:"状態",purpose:"設計の目的"}))add(label,k==="status"?statuses[p()[k]]:p()[k],k==="status"?statuses[other[k]]:other[k]);
  const vals=(a,b,prefix)=>pairRows(a,b,x=>x.name).forEach(([x,y])=>{const n=x?.name||y.name;add(`${prefix} / ${n}`,x?`${x.value||"未設定"}${x.unit?" "+x.unit:""}`:"（項目なし）",y?`${y.value||"未設定"}${y.unit?" "+y.unit:""}`:"（項目なし）");if(x?.name!==y?.name&&x&&y)add(`${prefix} / 項目名`,x.name,y.name);if(x?.source||y?.source)add(`${prefix} / ${n} / 出典`,x?.source,y?.source);if(x?.note||y?.note)add(`${prefix} / ${n} / 備考`,x?.note,y?.note);});
  vals(p().requirements,other.requirements,"要求仕様");
  pairRows(p().components,other.components,x=>x.kind+":"+x.name).forEach(([a,b])=>{const label=a?.name||b.name;add(`部品 / ${label}`,a?`${a.name} / ${kinds[a.kind]} / ${a.stage} / ${statuses[a.status]}`:"（部品なし）",b?`${b.name} / ${kinds[b.kind]} / ${b.stage} / ${statuses[b.status]}`:"（部品なし）");if(a?.owner||b?.owner)add(`${label} / 担当`,a?.owner,b?.owner);if(a?.description||b?.description)add(`${label} / メモ`,a?.description,b?.description);vals(a?.parameters||[],b?.parameters||[],label);});
  const changed=rows.filter(x=>x.a!==x.b).length;
  $("#comparison-result").innerHTML=panel(`比較結果 <span class="counts-label">${changed} / ${rows.length} 行に差分</span>`,`<div class="table-wrap"><table><thead><tr><th>項目</th><th>${esc(p().name)}（現在）</th><th>${esc(other.name)}</th></tr></thead><tbody>${rows.map(x=>`<tr class="${x.a!==x.b?"changed":""}"><td>${esc(x.label)}</td><td class="compare-cell">${emptyValue(x.a)}</td><td class="compare-cell">${emptyValue(x.b)}</td></tr>`).join("")}</tbody></table></div>`);
}
function field(name,label,value="",options={}){const attrs=`name="${name}" id="field-${name}" ${options.required?"required":""} maxlength="${options.max||2000}"`;const control=options.select?`<select ${attrs}>${Object.entries(options.select).map(([k,v])=>`<option value="${esc(k)}" ${value===k?"selected":""}>${esc(v)}</option>`).join("")}</select>`:options.area?`<textarea ${attrs} rows="${options.rows||4}">${esc(value)}</textarea>`:`<input ${attrs} value="${esc(value)}" ${options.placeholder?`placeholder="${esc(options.placeholder)}"`:""}>`;return `<label class="field" for="field-${name}"><span>${label}${options.required?" *":""}</span>${control}${options.hint?`<div class="hint-text">${options.hint}</div>`:""}</label>`;}
function modal(title,html,callback,submit="保存する"){$("#dialog-title").textContent=title;$("#dialog-fields").innerHTML=html;$("#form-error").textContent="";$("#dialog-submit").textContent=submit;$("#dialog-submit").disabled=false;formCallback=callback;formDirty=false;$("#editor").showModal();$("#dialog-fields input, #dialog-fields textarea, #dialog-fields select")?.focus();}

function encodeFile(file, label) {
  if (!(file instanceof File) || file.size === 0 || file.size > 5 * 1024 * 1024) {
    throw new Error(`${label}は1件5MB以内のJSONを選択してください。`);
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("ファイルを読み込めませんでした。"));
    reader.readAsDataURL(file);
  });
}

async function resultForm(kind) {
  const projectId = p().id, revision = current.revision;
  const [history, listed] = await Promise.all([
    api(`/api/history?id=${projectId}`),
    kind === "cycle" ? null : api(`/api/results?id=${projectId}`),
  ]);
  if (!current || p().id !== projectId || current.revision !== revision) return;
  const isBlade = kind === "blade";
  const isPerformance = kind === "performance";
  const shapes = listed?.results.filter(item=>item.kind === "blade" && item.stage1_share != null) || [];
  const revisions = Object.fromEntries(history.map(item => [item.revision, `Rev. ${item.revision}`]));
  const componentOptions = parts => ({"":"案件全体", ...Object.fromEntries(parts.map(part=>[part.id, part.name]))});
  const fileInput = (name, label) => `<label class="field"><span>${label} *</span><input type="file" name="${name}" accept=".json,application/json" required></label>`;
  const primaryLabel = {blade:"Blade DesignerプロジェクトJSON", cycle:"サイクルケースJSONまたは2段設計一式JSON",
                        performance:"Cycle Studioの比較一式JSON"}[kind];
  const title = {blade:"翼形状を取込", cycle:"サイクル結果を取込", performance:"性能比較を取込"}[kind];
  const shareFields = [
    {name:"equal_blade", label:"均等配分案の翼形状", share:.5},
    {name:"seventy_five_blade", label:"75%配分案の翼形状", share:.75},
  ];
  const blankOption = isBlade ? "新しい形状として登録" : "翼形状を選択";
  let shapeFields = "";
  if (isBlade) {
    shapeFields = field("replaces_blade", "差し替え元の翼形状（任意）", "", {select:{"":blankOption}});
  } else if (isPerformance) {
    shapeFields = '<p class="subtle">解析・試験で使った2案の翼形状を、同じ設計版・関連部品に先に登録してください。</p>' +
      shareFields.map(input=>field(input.name,input.label,"",{select:{"":blankOption}, required:true})).join("");
  }
  const fields = '<p class="subtle">成果物の入力元にあたる設計版と部品を選んでください。入力元の記載がない成果物は、この指定で紐付けます。</p>' +
    field("source_revision", "入力元の設計版", String(revision), {select: revisions}) +
    field("result_component", "関連部品", componentId, {select: componentOptions(p().components)}) +
    shapeFields + fileInput("primary", primaryLabel) + (isBlade ? fileInput("handoff", "対応する .handoff.json") : "");
  modal(title, fields, async data => {
    const payload = {id: projectId, revision, source_revision: Number(data.source_revision),
                     component_id: data.result_component, kind, primary: await encodeFile(data.primary, primaryLabel)};
    if (isBlade) {
      payload.handoff = await encodeFile(data.handoff, "手渡し記録");
      if (data.replaces_blade) payload.replaces_blade_id = data.replaces_blade;
    }
    if (isPerformance) payload.blade_ids = {equal:data.equal_blade, "75-percent":data.seventy_five_blade};
    const outcome = await api("/api/result-import", payload);
    renderTab();
    toast(outcome.created ? "設計成果物を登録しました" : `すでに Rev. ${outcome.result.source_revision} に登録されています`);
  }, "登録する");
  const revisionInput = $("#field-source_revision"), componentInput = $("#field-result_component");
  const shapeInputs = isBlade ? [{name:"replaces_blade", share:null}] : isPerformance ? shareFields : [];
  function updateShapeOptions() {
    const eligible = shapes.filter(item=>item.source_revision === Number(revisionInput.value) &&
      item.component_id === componentInput.value);
    for (const {name,share} of shapeInputs) {
      const select = $(`#field-${name}`);
      const value = select.value;
      const options = eligible.filter(item=>isBlade ? !item.replaced_by_blade_id : item.stage1_share === share);
      select.replaceChildren(new Option(blankOption,""), ...options.map(item=>new Option(
        `${item.name} · 第1段 ${item.stage1_share * 100}% · ${item.project_sha256.slice(0,10)}${item.replaced_by_blade_id ? " · 差し替え済み" : ""}`,
        item.id)));
      if (options.some(item=>item.id === value)) select.value = value;
    }
  }
  updateShapeOptions();
  componentInput.addEventListener("change",updateShapeOptions);
  let generation = 0;
  revisionInput.addEventListener("change", async () => {
    const request = ++generation, value = revisionInput.value;
    const error = $("#form-error"), submit = $("#dialog-submit");
    submit.disabled = componentInput.disabled = true;
    error.textContent = "";
    try {
      const parts = await api(`/api/result-targets?id=${projectId}&revision=${encodeURIComponent(value)}`);
      if (request !== generation || !revisionInput.isConnected || !$("#editor").open) return;
      const previous = componentInput.value;
      componentInput.replaceChildren(...Object.entries(componentOptions(parts)).map(([key, label])=>new Option(label, key)));
      if (parts.some(part=>part.id === previous)) componentInput.value = previous;
      updateShapeOptions();
      submit.disabled = componentInput.disabled = false;
    } catch (e) {
      if (request === generation && revisionInput.isConnected && $("#editor").open) error.textContent = e.message;
    }
  });
}
function confirmAction(message){return new Promise(resolve=>{const dialog=$("#confirmation");$("#confirm-message").textContent=message;const finish=value=>{dialog.close();dialog.oncancel=null;resolve(value);};$("#confirm-yes").onclick=()=>finish(true);$("#confirm-no").onclick=()=>finish(false);dialog.oncancel=event=>{event.preventDefault();finish(false);};dialog.showModal();$("#confirm-no").focus();});}
async function closeModal(){if(formBusy)return;if(formDirty&&!await confirmAction("入力中の内容を破棄して閉じますか？"))return;$("#editor").close();formDirty=false;}
async function saveDraft(draft){current=await api("/api/save",{project:draft,revision:current.revision});await refresh();if(componentId&&!p().components.some(c=>c.id===componentId))componentId="";render();toast("設計情報を保存しました");}
function projectForm(isNew){const x=isNew?{}:p();modal(isNew?"新規プロジェクト":"プロジェクトの基本情報",field("name","案件名",x.name,{required:true,max:150,placeholder:"例：小型発電用ガスタービン"})+`<div class="form-grid">${field("code","案件番号",x.code,{max:80})}${isNew?field("template","初期構成","single_shaft",{select:{single_shaft:"一軸ガスタービン（値は未設定）",blank:"空のプロジェクト"}}):field("status","状態",x.status,{select:statuses})}</div>`+(isNew?"":`<div class="form-grid">${field("application","用途",x.application,{max:100,placeholder:"発電用・航空用・研究用など"})}${field("architecture","エンジン構成",x.architecture,{max:150})}</div>${field("owner","担当",x.owner,{max:100})}${field("purpose","設計の目的・要求",x.purpose,{area:true,max:10000})}`),async data=>{if(isNew){current=await api("/api/new",data);activeTab="overview";componentId="";comparison=null;await refresh();render();toast("プロジェクトを作成しました");}else{const draft=structuredClone(p());Object.assign(draft,data);await saveDraft(draft);}},isNew?"作成する":"保存する");}
function clearStationComponent(draft, componentId) {
  for (const item of draft.stations || []) {
    if (item.component_id === componentId) item.component_id = "";
  }
}
function componentForm(cid){const x=p().components.find(c=>c.id===cid)||{id:id(),kind:"compressor",status:"draft",parameters:[]};modal(cid?"部品情報を編集":"部品を追加",field("name","部品名",x.name,{required:true,max:150})+`<div class="form-grid">${field("kind","種別",x.kind,{select:kinds})}${field("status","状態",x.status,{select:statuses})}${field("stage","段・位置",x.stage,{max:100})}${field("owner","担当",x.owner,{max:100})}</div>`+field("description","部品メモ・接続関係",x.description,{area:true,max:10000}),async data=>{
  const draft=structuredClone(p());const item={...x,...data};
  if(cid){
    draft.components[draft.components.findIndex(c=>c.id===cid)]=item;
    if(item.kind!==x.kind) clearStationComponent(draft, cid);
  }else draft.components.push(item);
  await saveDraft(draft);
});}
function parameterForm(paramId){const arr=selected()?.parameters||p().requirements;const x=arr.find(v=>v.id===paramId)||{id:id()};modal(paramId?"設計項目を編集":"設計項目を追加",field("name","項目名",x.name,{required:true,max:150})+`<div class="form-grid">${field("value","値",x.value,{max:1000,placeholder:"未定なら空欄のまま登録できます"})}${field("unit","単位・定義",x.unit,{max:60,placeholder:"例：kPa（全圧）"})}</div>`+field("source","出典・根拠",x.source,{max:2000,placeholder:"要求仕様書・仮定・試験値・資料の章番号など"})+field("note","備考・適用条件",x.note,{area:true,max:4000}),async data=>{const draft=structuredClone(p());const values=componentId?draft.components.find(c=>c.id===componentId).parameters:draft.requirements;const item={...x,...data};if(paramId)values[values.findIndex(v=>v.id===paramId)]=item;else values.push(item);await saveDraft(draft);});}
function noteForm(nid){const x=p().notes.find(n=>n.id===nid)||{id:id(),created_at:new Date().toISOString()};modal(nid?"設計メモを編集":"設計メモを追加",field("title","件名",x.title,{required:true,max:150})+field("body","設計判断・検討内容",x.body,{area:true,rows:9,max:20000}),async data=>{const draft=structuredClone(p());const item={...x,...data};if(nid)draft.notes[draft.notes.findIndex(n=>n.id===nid)]=item;else draft.notes.unshift(item);await saveDraft(draft);});}
function linkForm(lid){const x=p().links.find(l=>l.id===lid)||{id:id(),component_id:componentId};modal(lid?"参考リンクを編集":"参考リンクを追加",field("title","資料名",x.title,{required:true,max:150})+field("url","URL",x.url,{required:true,max:2000,placeholder:"https://…"})+field("component_id","関連部品",x.component_id,{select:{"":"案件全体",...Object.fromEntries(p().components.map(c=>[c.id,c.name]))}})+field("note","備考",x.note,{area:true,max:4000}),async data=>{const draft=structuredClone(p());const item={...x,...data};if(lid)draft.links[draft.links.findIndex(l=>l.id===lid)]=item;else draft.links.push(item);await saveDraft(draft);});}
async function removeItem(type,itemId){const messages={component:"この部品と部品の設計値を削除します。関連資料とリンクは案件全体に移し、評価断面の対象部品は未指定にします。",parameter:"この設計項目を削除します。",note:"このメモを削除します。",link:"この参考リンクを削除します。",file:"添付資料の登録を解除します。過去の履歴には残ります。"};if(!await confirmAction(messages[type]+"\n変更履歴から復元できます。続けますか？"))return;const draft=structuredClone(p());if(type==="component"){draft.components=draft.components.filter(x=>x.id!==itemId);[...draft.links,...draft.attachments].forEach(x=>{if(x.component_id===itemId)x.component_id="";});clearStationComponent(draft,itemId);}else if(type==="parameter"){if(componentId){const c=draft.components.find(c=>c.id===componentId);c.parameters=c.parameters.filter(x=>x.id!==itemId);}else draft.requirements=draft.requirements.filter(x=>x.id!==itemId);}else{const key={note:"notes",link:"links",file:"attachments"}[type];draft[key]=draft[key].filter(x=>x.id!==itemId);}await saveDraft(draft);}
let actionBusy=false;
document.addEventListener("click",async event=>{const el=event.target.closest("[data-action]");if(!el||actionBusy)return;const action=el.dataset.action;try{actionBusy=true;
  if(action==="home")home();
  else if(action==="new-project")projectForm(true);
  else if(action==="open")await openProject(el.dataset.id);
  else if(action==="edit-project")projectForm(false);
  else if(action==="tab"){activeTab=el.dataset.tab;render();}
  else if(action==="select-component"){componentId=el.dataset.id;activeTab="parameters";render();}
  else if(action==="new-component"||action==="edit-component")componentForm(el.dataset.id);
  else if(action==="new-parameter"||action==="edit-parameter")parameterForm(el.dataset.id);
  else if(action==="new-station"||action==="edit-station")stationForm(el.dataset.id);
  else if(action==="station-preset")stationPresetForm();
  else if(action==="remove-station")await removeStation(el.dataset.id);
  else if(action==="new-note"||action==="edit-note")noteForm(el.dataset.id);
  else if(action==="new-link"||action==="edit-link")linkForm(el.dataset.id);
  else if(action.startsWith("delete-"))await removeItem(action.slice(7),el.dataset.id);
  else if(action==="move-component"){const draft=structuredClone(p()),i=draft.components.findIndex(c=>c.id===el.dataset.id),j=i+Number(el.dataset.direction);if(j>=0&&j<draft.components.length){[draft.components[i],draft.components[j]]=[draft.components[j],draft.components[i]];await saveDraft(draft);}}
  else if(action==="clone")modal("設計案を複製",field("name","新しい設計案の名前",p().name.slice(0,140)+"（別案）",{required:true,max:150})+'<p class="subtle">現在の設計情報と添付資料を複製します。元の案件は保持します。</p>',async data=>{current=await api("/api/clone",{id:p().id,name:data.name});componentId="";activeTab="overview";comparison=null;await refresh();render();toast("設計案を複製しました");},"複製する");
  else if(action==="restore"){if(await confirmAction(`Rev. ${el.dataset.revision} の状態を新しいリビジョンとして保存します。続けますか？`)){current=await api("/api/restore",{id:p().id,revision:current.revision,history_id:Number(el.dataset.id)});if(componentId&&!selected())componentId="";await refresh();render();toast("履歴を復元しました");}}
  else if(action==="import")$("#import-file").click();
  else if(["import-cycle-result","import-blade-result","import-performance-result"].includes(action))await resultForm(action.split("-")[1]);
  else if(action==="upload")$("#attachment-file").click();
}catch(e){toast(e.message,true);}finally{actionBusy=false;}});
$("#editor-form").addEventListener("input",()=>formDirty=true);
$("#editor-form").addEventListener("change",()=>formDirty=true);
$("#editor-form").addEventListener("submit",async event=>{event.preventDefault();if(formBusy||$("#dialog-submit").disabled)return;formBusy=true;$("#dialog-submit").disabled=true;$("#dialog-close").disabled=true;$("#dialog-cancel").disabled=true;try{await formCallback(Object.fromEntries(new FormData(event.currentTarget)));formDirty=false;$("#editor").close();}catch(e){$("#form-error").textContent=e.message;}finally{formBusy=false;$("#dialog-submit").disabled=false;$("#dialog-close").disabled=false;$("#dialog-cancel").disabled=false;}});
$("#dialog-close").onclick=closeModal;$("#dialog-cancel").onclick=closeModal;
$("#editor").addEventListener("cancel",event=>{event.preventDefault();closeModal();});
window.addEventListener("beforeunload",event=>{if(formDirty||formBusy){event.preventDefault();event.returnValue="";}});
$("#home").onclick=home;$("#brand").onclick=event=>{event.preventDefault();home();};
document.addEventListener("input",event=>{if(event.target.id==="project-search"){search=event.target.value;renderCards();}});
document.addEventListener("change",async event=>{try{if(event.target.id==="status-filter"){filter=event.target.value;renderCards();}else if(event.target.id==="component-scope"){componentId=event.target.value;nav();renderTab();}else if(event.target.id==="compare-target"){const pid=p().id,otherId=event.target.value;comparison=null;if(!otherId)return renderTab();const env=await api("/api/project?id="+otherId);if(current&&p().id===pid&&activeTab==="compare"&&$("#compare-target").value===otherId){comparison=env;renderComparison();}}}catch(e){toast(e.message,true);}});
$("#import-file").addEventListener("change",async event=>{const file=event.target.files[0];event.target.value="";if(!file)return;try{if(file.size>45*1024*1024)throw new Error("案件JSONは45MB以内にしてください。");const bundle=JSON.parse(await file.text());current=await api("/api/import",{bundle});componentId="";activeTab="overview";comparison=null;await refresh();render();toast("新しい案件として取り込みました");}catch(e){toast(e.message,true);}});
$("#attachment-file").addEventListener("change",async event=>{const file=event.target.files[0];event.target.value="";if(!file||!current)return;const projectId=p().id,revision=current.revision,cid=componentId;try{if(file.size>5*1024*1024)throw new Error("添付ファイルは1件5MB以内です。");const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(",")[1]);reader.onerror=()=>reject(new Error("ファイルを読み込めませんでした。"));reader.readAsDataURL(file);});const env=await api("/api/upload",{id:projectId,revision,name:file.name,data,component_id:cid});await refresh();if(current&&p().id===projectId){current=env;render();}toast("資料のコピーを保存しました");}catch(e){toast(e.message,true);}});
refresh().then(()=>{nav();renderHome();}).catch(e=>{$("#content").textContent="読み込めませんでした。ページを再読込してください。 "+e.message;});
