const mbse = (() => {
  const labels = {modes:"運転モード", functions:"機能", interfaces:"機器間接続", traces:"要求の関連付け", cases:"検証ケース"};
  const methods = {analysis:"解析", test:"試験", inspection:"検査", demonstration:"実演"};
  const metrics = {"":"手動判定・資料の確認値", net_power:"正味電力 [kW]",
    cold_oil_a:"冷間・軸受Aの給油流量余裕 [L/min]", cold_oil_b:"冷間・軸受Bの給油流量余裕 [L/min]"};
  const comparisons = {"":"手順に従い判定", ge:"要求値以上", le:"要求値以下"};
  const states = {pending:"未評価", stale:"再検証が必要", pass:"適合", fail:"不適合", inconclusive:"判定保留"};
  const model = () => Object.fromEntries(Object.keys(labels).map(key => [key, p().mbse?.[key] || []]));
  const byId = (rows, key) => rows.find(row => row.id === key);
  const nameOf = (rows, key) => byId(rows, key)?.name || "";
  const reqName = (rows, trace) => `${trace.code} · ${nameOf(rows, trace.id)}`;
  const dataAttrs = (group, key="") => `data-group="${group}" data-id="${key}"`;
  const editButton = (group, key, title="編集") => button("mbse-edit", title, dataAttrs(group, key), "small");
  const addButton = group => editButton(group, "", `＋ ${labels[group]}を追加`);
  const removeButton = (group, key, title="登録を解除") => button("mbse-remove", title, dataAttrs(group, key), "small danger");
  const selectOptions = (rows, blank) => Object.fromEntries([
    ...(blank === undefined ? [] : [["", blank]]),
    ...rows.map(row => [row.id,row.name]),
  ]);
  const parameterRows = project => project.components.flatMap(part => part.parameters.map(value => ({
    id:value.id, name:`${part.name} / ${value.name}`, detail:[value.value,value.unit].filter(Boolean).join(" "),
  })));

  function table(headers, rows, missing) {
    if (!rows.length) return `<p class="panel-body subtle">${missing}</p>`;
    return `<div class="table-wrap"><table><thead><tr>${headers.map(label => `<th scope="col">${label}</th>`).join("")}</tr>
      </thead><tbody>${rows.map(row => `<tr${row.id ? ` id="${row.id}"` : ""}>${row.cells.map(value => `<td>${value}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function names(rows, ids) {
    return ids.map(key => nameOf(rows,key)).filter(Boolean).map(esc).join("、") || "—";
  }

  function evidenceRow(record, historical=false) {
    const source = {synthetic:"仮想", ...methods}[record.source_kind];
    const value = record.value == null ? "" : ` · ${new Intl.NumberFormat("ja-JP",{maximumSignificantDigits:9}).format(record.value)}
      ${esc(record.unit)}（基準 ${record.threshold} ${esc(record.unit)}）`;
    return `<li><strong>${historical ? esc(record.requirement_code+" / "+record.case_name)+" · " : ""}${states[record.verdict]}</strong>
      <span class="subtle"> · ${source}${value} · 設計 Rev. ${record.design_revision} / 根拠 Rev. ${record.source_revision}</span>
      <div><a href="/api/mbse-file?project=${p().id}&amp;id=${record.id}" download>${esc(record.artifact_name)}（${esc(record.file_name)}）</a></div>
      <details class="mbse-detail"><summary>判定条件と根拠</summary><p>${esc(record.mode)} · ${esc(record.conditions)}</p>
      <p>${esc(record.note)}</p><p class="subtle mbse-hash">根拠ファイル SHA-256 ${record.artifact_sha256}</p>
      ${record.cycle_source ? `<p class="subtle mbse-hash">元サイクル実行ID ${esc(record.cycle_source.run_id)}<br>
        元サイクル入力 SHA-256 ${esc(record.cycle_source.input_hash)}</p>` : ""}</details></li>`;
  }

  function caseView(item, evaluation, modeName) {
    const records = evaluation?.records || [];
    const state = evaluation?.state || "pending";
    const tone = {pass:"designing", fail:"mbse-fail", stale:"reviewing", inconclusive:"reviewing"}[state] || "";
    const stateText = records[0]?.source_kind === "synthetic" && state !== "stale" ? `仮想 · ${states[state]}` : states[state];
    return `<div class="mbse-case"><span class="badge ${tone}">${stateText}</span> <strong>${esc(item.name)}</strong>
      <div class="subtle">${esc(modeName)} · ${methods[item.method]} · ${comparisons[item.comparison]}
      ${item.metric ? ` · ${esc(metrics[item.metric])}` : ""}</div>
      <details class="mbse-detail"><summary>検証手順・合否基準</summary><p>${esc(item.procedure)}</p></details>
      <div class="heading-actions">${button("mbse-record","判定を登録",`data-id="${item.id}"`,"small")}
      ${editButton("cases",item.id)}${removeButton("cases",item.id,"ケースを削除")}</div>
      ${records.length ? `<details class="mbse-detail"><summary>判定履歴 ${records.length}件</summary>
      <ul class="mbse-records">${records.map(record => evidenceRow(record)).join("")}</ul></details>` : ""}</div>`;
  }

  function renderRequirements(target, data, filter="") {
    const project = p(), m = model(), parameters = parameterRows(project);
    const evaluations = new Map(data.cases.map(row => [row.id,row]));
    const traces = new Map(m.traces.map(row => [row.id,row]));
    const children = new Map();
    for (const trace of m.traces) {
      for (const parent of trace.parent_ids) {
        if (!children.has(parent)) children.set(parent, []);
        children.get(parent).push(trace.id);
      }
    }
    const requirementButtons = ids => ids.map(key => button("mbse-focus", esc(nameOf(project.requirements,key)),
      `data-id="${key}"`, "small")).join(" ");
    const rows = [];
    for (const requirement of project.requirements) {
      const trace = traces.get(requirement.id);
      const cases = m.cases.filter(item => item.requirement_id === requirement.id);
      const direct = [...(trace?.component_ids || []), ...(trace?.parameter_ids || []),
        ...m.functions.filter(item => trace?.function_ids.includes(item.id)).flatMap(item => item.component_ids),
        ...m.interfaces.filter(item => trace?.interface_ids.includes(item.id)).flatMap(item => [item.from_component_id,item.to_component_id])];
      const affected = cases.some(item => {
        const status = evaluations.get(item.id);
        return status && [...status.component_ids, ...status.parameter_ids].includes(filter);
      });
      if (filter && !direct.includes(filter) && !affected) continue;
      const relations = trace ? `<dl class="mbse-links">
        <dt>親要求</dt><dd>${requirementButtons(trace.parent_ids) || "—"}</dd>
        <dt>派生要求</dt><dd>${requirementButtons(children.get(requirement.id) || []) || "—"}</dd>
        <dt>関連機能</dt><dd>${names(m.functions,trace.function_ids)}</dd>
        <dt>機器間接続</dt><dd>${names(m.interfaces,trace.interface_ids)}</dd>
        <dt>根拠</dt><dd>${esc(trace.rationale) || "—"}</dd></dl>` : "";
      const parts = trace?.component_ids.map(key => button("select-component",esc(nameOf(project.components,key)),`data-id="${key}"`,"small")).join(" ");
      const inputs = trace?.parameter_ids.map(key => {
        const item = byId(parameters,key);
        const part = project.components.find(component => component.parameters.some(value => value.id === key));
        return `<li>${button("mbse-parameter",esc(item.name),`data-id="${key}" data-component="${part.id}"`,"small")} ${esc(item.detail)}</li>`;
      }).join("");
      const detail = `<div>${esc(requirement.value)} ${esc(requirement.unit)}</div>`
        + (requirement.source ? `<div class="subtle">出典 ${esc(requirement.source)}</div>` : "")
        + (requirement.note ? `<div class="subtle">${esc(requirement.note)}</div>` : "");
      const controls = editButton("traces",requirement.id,trace ? "要求・関連付けを編集" : "要求を関連付ける")
        + (trace ? removeButton("traces",trace.id,"関連付けを解除") : "");
      rows.push({id:`mbse-requirement-${requirement.id}`,cells:[
        `<strong>${trace ? esc(trace.code)+" · " : ""}${esc(requirement.name)}</strong>${detail}
        <div class="subtle">${trace?.approval ? `承認記録 ${esc(trace.approval)}` : "承認記録なし"}</div><div class="heading-actions">${controls}</div>`,
        trace ? `<div>${names(m.modes,trace.mode_ids)}</div><details class="mbse-detail"><summary>要求・機能・接続をたどる</summary>${relations}</details>`
          : (children.has(requirement.id) ? requirementButtons(children.get(requirement.id)) : "未設定"),
        `<div class="heading-actions">${parts || ""}</div>${inputs ? `<ul class="mbse-inputs">${inputs}</ul>` : "—"}`,
        `${cases.map(item => caseView(item,evaluations.get(item.id),nameOf(m.modes,item.mode_id))).join("") || '<div class="subtle">検証ケース未設定</div>'}
        ${trace?.mode_ids.length ? button("mbse-add-case","＋ 検証ケース",`data-id="${trace.id}"`,"small") : ""}`,
      ]});
    }
    target.innerHTML = table(["要求・出典", "運転モードと関連", "担当部品・設計値", "検証と根拠"], rows,
      filter ? "この対象に結び付いた要求はありません。" : "「要求の追加」から登録できます。既存案件の要求もここに表示します。");
  }

  function architecture(project, m) {
    const rowActions = (group,item) => `<div class="heading-actions">${editButton(group,item.id)}${removeButton(group,item.id)}</div>`;
    const modes = m.modes.map(item => ({cells:[esc(item.name),esc(item.purpose),esc(item.conditions),rowActions("modes",item)]}));
    const functions = m.functions.map(item => ({cells:[esc(item.name),esc(item.description),names(m.modes,item.mode_ids),
      names(project.components,item.component_ids),rowActions("functions",item)]}));
    const endpoint = (item, end) => {
      const station = byId(project.stations || [],item[`${end}_station_id`]);
      return `<span class="flow-node">${esc(nameOf(project.components,item[`${end}_component_id`]))}
        ${station ? `<small>${esc(station.code)}</small>` : ""}</span>`;
    };
    const interfaces = m.interfaces.map(item => ({cells:[
      `<strong>${esc(item.name)}</strong><div class="flow-strip mbse-flow">${endpoint(item,"from")}<span aria-label="から" class="flow-arrow">→</span>${endpoint(item,"to")}</div>`,
      esc(item.quantities),names(m.modes,item.mode_ids),rowActions("interfaces",item),
    ]}));
    return panel("運用目的・運転モード",table(["運転モード","運用目的","適用条件","操作"],modes,"定格・冷間始動などの運転モードを登録します。"),addButton("modes"))
      + panel("機能と部品の割当",table(["機能","説明","運転モード","担当部品","操作"],functions,"機能と、その機能を担う部品を登録します。"),addButton("functions"))
      + panel("機器間の接続図",table(["接続・流れ方向","受け渡す量と単位","運転モード","操作"],interfaces,
        "送出部品と受入部品を結び付けます。評価断面の対応も指定できます。"),addButton("interfaces"));
  }

  async function render(target, generation, projectId) {
    target.innerHTML = '<p class="subtle">要求と検証記録を読み込んでいます…</p>';
    try {
      const data = await api(`/api/mbse-evidence?id=${projectId}`);
      if (!current || p().id !== projectId || generation !== listGeneration) return;
      if (data.revision !== current.revision) {
        target.textContent = "別の画面で案件が更新されました。再読込してから開いてください。";
        return;
      }
      const showFilter = p().components.length ? `<div class="panel-body mbse-filter"><label for="mbse-impact">変更の影響を確認</label>
        <select id="mbse-impact"><option value="">全ての要求</option><optgroup label="部品">${p().components.map(part =>
          `<option value="${part.id}">${esc(part.name)}</option>`).join("")}</optgroup><optgroup label="設計値">${parameterRows(p()).map(value =>
          `<option value="${value.id}">${esc(value.name)}</option>`).join("")}</optgroup></select></div>` : "";
      target.innerHTML = panel("要求検証マトリクス",showFilter+'<div id="mbse-matrix"></div>',editButton("traces","","＋ 要求の追加"))
        + architecture(p(),model())
        + (data.archived.length ? panel("削除した検証ケースの判定履歴",
          `<ul class="panel-body mbse-records">${data.archived.map(record => evidenceRow(record,true)).join("")}</ul>`) : "");
      const matrix = target.querySelector("#mbse-matrix");
      renderRequirements(matrix,data);
      target.querySelector("#mbse-impact")?.addEventListener("change",event => renderRequirements(matrix,data,event.target.value));
    } catch (error) {
      if (target.isConnected && generation === listGeneration) target.textContent = error.message;
    }
  }

  function checkGroup(name,label,choices,values) {
    return `<fieldset class="mbse-check-group"><legend>${label}</legend>${choices.length ? choices.map(item =>
      `<label><input type="checkbox" name="${name}" value="${item.id}" ${values?.includes(item.id) ? "checked" : ""}> ${esc(item.name)}</label>`).join("")
      : '<div class="subtle">選択できる項目がありません。</div>'}</fieldset>`;
  }

  function edit(group,key="",requirementId="") {
    const m = model(), project = p(), partOptions = selectOptions(project.components,"部品を選択");
    const row = byId(m[group],key) || {id:key || id()};
    const requirement = byId(project.requirements, row.id) || {id:row.id};
    const arrays = [];
    function list(name,label,choices) {
      arrays.push(name);
      return checkGroup(name,label,choices,row[name]);
    }
    const multi = () => list("mode_ids","適用する運転モード",m.modes);
    const name = field("name", group === "traces" ? "要求名" : `${labels[group]}名`,
      group === "traces" ? requirement.name : row.name,{required:true,max:150});
    let fields = name;
    if (group === "modes") {
      fields += field("purpose","運用目的",row.purpose,{required:true,max:1000,area:true})
        + field("conditions","運転条件",row.conditions,{required:true,area:true,max:2000});
    } else if (group === "functions") {
      fields += field("description","機能の説明",row.description,{area:true,max:2000})
        + multi() + list("component_ids","割り当てる部品",project.components);
    } else if (group === "interfaces") {
      const stationOptions = selectOptions((project.stations || []).map(item => ({...item,name:`${item.code} · ${item.name}`})),"断面は任意");
      fields += `<div class="form-grid">${field("from_component_id","送出部品",row.from_component_id,{required:true,select:partOptions})}
        ${field("to_component_id","受入部品",row.to_component_id,{required:true,select:partOptions})}
        ${field("from_station_id","送出断面",row.from_station_id,{select:stationOptions})}
        ${field("to_station_id","受入断面",row.to_station_id,{select:stationOptions})}</div>`
        + field("quantities","受け渡す量・単位・圧力の定義",row.quantities,{required:true,area:true,max:1000}) + multi();
    } else if (group === "traces") {
      fields = field("code","要求番号",row.code,{required:true,max:40,placeholder:"REQ-GT-001"})
        + name + `<div class="form-grid">${field("value","要求値",requirement.value,{max:1000})}
        ${field("unit","単位",requirement.unit,{max:60})}</div>`
        + field("source","要求の出典",requirement.source,{max:2000})
        + field("note","補足・適用条件",requirement.note,{area:true,max:4000})
        + field("approval","承認記録（文書・版など）",row.approval,{max:200})
        + list("parent_ids","親要求（派生元）",project.requirements.filter(item=>item.id!==row.id))
        + multi() + list("component_ids","担当部品",project.components)
        + list("function_ids","関連機能",m.functions) + list("interface_ids","機器間接続",m.interfaces)
        + list("parameter_ids","関連する設計値",parameterRows(project))
        + field("rationale","割当・派生の根拠",row.rationale,{area:true,max:2000});
    } else {
      const trace = byId(m.traces,row.requirement_id || requirementId);
      const requires = selectOptions(m.traces.map(item => ({id:item.id,name:reqName(project.requirements,item)})));
      fields += field("requirement_id","対象要求",trace?.id,{select:requires,required:true})
        + field("mode_id","検証の運転モード",row.mode_id,{select:selectOptions(m.modes),required:true})
        + `<div class="form-grid">${field("method","検証方法",row.method || "analysis",{select:methods})}
        ${field("comparison","判定条件",row.comparison,{select:comparisons})}</div>`
        + field("metric","定格通しケースから取得する指標",row.metric,{select:metrics})
        + field("procedure","検証手順・合否基準",row.procedure,{required:true,area:true,max:2000});
    }
    modal(key ? labels[group]+"を編集" : labels[group]+"を登録", fields, async data => {
      const draft = structuredClone(p());
      draft.mbse ??= model();
      const form = new FormData($("#editor-form"));
      const item = {id:row.id,...data};
      for (const fieldName of arrays) item[fieldName] = form.getAll(fieldName);
      if (group === "traces") {
        const updated = {id:row.id};
        for (const key of ["name","value","unit","source","note"]) {
          updated[key] = item[key];
          delete item[key];
        }
        const index = draft.requirements.findIndex(current => current.id === row.id);
        if (index < 0) draft.requirements.push(updated);
        else draft.requirements[index] = updated;
      }
      const index = draft.mbse[group].findIndex(item => item.id === row.id);
      if (index < 0) draft.mbse[group].push(item);
      else draft.mbse[group][index] = item;
      await saveDraft(draft);
    });
    if (group === "cases") {
      const selected = $("#field-requirement_id"), modes = $("#field-mode_id");
      const setModes = () => {
        const modeIds = byId(m.traces, selected.value).mode_ids;
        const before = modes.value;
        modes.replaceChildren(...m.modes.filter(item => modeIds.includes(item.id))
          .map(item => new Option(item.name,item.id,false,item.id === before)));
      };
      selected.addEventListener("change",setModes);
      setModes();
    }
  }

  async function remove(group,key) {
    if (!await confirmAction(`${labels[group]}の登録を解除します。以前の検証の判定履歴は保持します。`)) return;
    const draft = structuredClone(p());
    draft.mbse[group] = draft.mbse[group].filter(row => row.id !== key);
    await saveDraft(draft);
  }

  async function record(key) {
    const item = byId(model().cases,key), projectId = p().id, revision = current.revision;
    const mode = nameOf(model().modes,item.mode_id);
    const responses = item.method === "analysis" ? await api(`/api/results?id=${projectId}`) : {results:[]};
    if (!current || p().id !== projectId || current.revision !== revision) return;
    const options = {};
    for (const result of responses.results.filter(row => row.kind === "suite" && row.bundle_available)) {
      options[`result|${result.id}`] = `${result.name} · Rev. ${result.source_revision}`;
    }
    if (!item.metric) {
      for (const file of p().attachments) options[`attachment|${file.id}`] = `資料 · ${file.name}`;
    }
    if (!Object.keys(options).length) throw new Error(item.metric
      ? "設計成果物で、定格通しケースと詳細JSON一式を登録してください。"
      : "根拠ファイルを資料または設計成果物に登録してください。");
    const provenance = field("source_kind","添付資料の出典",item.method,{select:{[item.method]:methods[item.method],synthetic:"仮想"}});
    const unit = byId(p().requirements,item.requirement_id).unit;
    const observed = field("observed",`資料から読み取った確認値（${esc(unit)}）`,"",{required:true,max:100});
    const verdict = field("verdict","判定","inconclusive",{select:{inconclusive:"判定保留",pass:"適合",fail:"不適合"}});
    modal("検証結果を登録",
      `<p class="hint">${esc(item.name)} · ${esc(mode)} · ${methods[item.method]}</p>`
      + field("artifact","根拠ファイル",Object.keys(options)[0],{select:options,required:true})
      + (!item.metric ? `<div id="mbse-manual-source">${provenance}</div>` : "")
      + (item.metric ? "" : item.comparison ? observed : verdict)
      + '<label class="mbse-confirm"><input type="checkbox" name="conditions_confirmed" required> 根拠と検証ケースの運転条件・構成を照合した</label>'
      + field("note","条件の照合・判断根拠","",{required:true,area:true,max:2000}), async data => {
        const [kind, artifactId] = data.artifact.split("|");
        await api("/api/mbse-evidence",{...data,id:projectId,revision,case_id:key,artifact_id:artifactId,artifact_kind:kind,
          conditions_confirmed:data.conditions_confirmed === "on"});
        renderTab();
        toast("検証結果を登録しました");
      });
    const selector = $("#field-artifact"), manual = $("#mbse-manual-source");
    if (manual) {
      const update = () => {manual.hidden = !selector.value.startsWith("attachment|");};
      selector.addEventListener("change",update);
      update();
    }
  }

  function focus(key) {
    const filter = $("#mbse-impact");
    if (filter?.value) {
      filter.value = "";
      filter.dispatchEvent(new Event("change"));
    }
    const row = $(`#mbse-requirement-${key}`);
    row?.scrollIntoView({block:"center"});
    row?.querySelector("button")?.focus({preventScroll:true});
  }

  return {render,edit,remove,record,focus};
})();
