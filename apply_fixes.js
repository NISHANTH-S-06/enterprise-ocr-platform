const fs = require('fs');

// 1. Update app/main.py
let mainPy = fs.readFileSync('app/main.py', 'utf8');
mainPy = mainPy.replace(
  'app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_methods=["*"], allow_headers=["*"])',
  'app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition"])'
);
fs.writeFileSync('app/main.py', mainPy);
console.log('Updated app/main.py');

// 2. Update frontend/index.html
let html = fs.readFileSync('frontend/index.html', 'utf8');

// Fix A: CSS Dropdown look
html = html.replace(/[ \t]*\[data-main-form-panel\] \.form-section:first-of-type \.fg > div\{[\s\S]*?\}\n/, '');
html = html.replace(/[ \t]*\[data-main-form-panel\] \.form-section:first-of-type input\{[\s\S]*?\}\n/, '');

// Fix B: Recursive unwrap in getOcrFieldValue
const newGetOcrFieldValue = `function extractValueFromObject(obj) {
  if (obj === null || obj === undefined) return "";
  if (typeof obj === "string" || typeof obj === "number") return String(obj).trim();
  if (typeof obj === "object") {
    const valueKeys = ["value","text","raw_value","normalized_value","extracted_value"];
    for (const key of valueKeys) {
      if (obj[key] !== undefined && obj[key] !== null) {
        const extracted = extractValueFromObject(obj[key]);
        if (extracted && extracted !== "[object Object]") return extracted;
      }
    }
    const firstKey = Object.keys(obj)[0];
    if (firstKey && typeof obj[firstKey] === "object") {
       return extractValueFromObject(obj[firstKey]);
    }
  }
  return "";
}

function getOcrFieldValue(field){
  const meta = {value:"", confidence:null, status:"", source:""};
  if(field === null || field === undefined) return meta;
  if(typeof field === "string" || typeof field === "number"){
    meta.value = String(field).trim();
    return meta;
  }
  if(typeof field === "object"){
    meta.value = extractValueFromObject(field);
    meta.confidence = field.confidence ?? field.score ?? null;
    meta.status = field.status || "";
    meta.source = field.source || field.method || field.engine || "";
  }
  return meta;
}`;

html = html.replace(/function getOcrFieldValue\(field\)\{[\s\S]*?return meta;\n\}/, newGetOcrFieldValue);

// Fix C: Export Blocked Reason
const oldExportCatch = `if(!res.ok){
    let message = \`\${res.status}\`;
    try{
      const error = await res.json();
      const detail = error.detail || error;
      message = detail.message || JSON.stringify(detail);
      updateMainExportReview(module, ["Export status: blocked", ...(detail.blocked_fields || []).map(field => \`Blocked field: \${field}\`), ...(detail.warnings || []).map(w => \`Warning: \${w}\`)]);
    }catch{ message += " - Export request failed"; }
    throw new Error(message);
  }`;

const newExportCatch = `if(!res.ok){
    let message = \`\${res.status}\`;
    let isBlocked = false;
    try{
      const error = await res.json();
      const detail = error.detail || error;
      message = detail.message || JSON.stringify(detail);
      if(detail.blocked) isBlocked = true;
      const blockers = detail.blocker_reasons || [];
      const blockerMsgs = blockers.map(b => \`Blocked: \${b.field_key || b.field} - \${b.reason}\`);
      const simpleBlocked = (detail.blocked_fields || []).filter(f => !blockers.some(b => b.field_key === f || b.field === f)).map(f => \`Blocked field: \${f}\`);
      updateMainExportReview(module, ["Export status: blocked", ...blockerMsgs, ...simpleBlocked, ...(detail.warnings || []).map(w => \`Warning: \${w}\`)]);
    }catch{ message += " - Export request failed"; }
    const err = new Error(message);
    err.isBlocked = isBlocked;
    throw err;
  }`;

html = html.replace(oldExportCatch, newExportCatch);

// Also fix the exportMainWorkbenchFile catch to use err.isBlocked
const oldExportFileCatch = `}catch(err){
    const existing = MAIN_WORKBENCH_EXPORT_STATE[module] || [];
    updateMainExportReview(module, existing.concat([\`Download status: failed\`, \`Error: \${err.message}\`]));
    setMainWorkbenchStatus(module, \`Export failed: \${err.message}\`, "error");
    toast("Export failed: " + err.message, "error");
  }`;

const newExportFileCatch = `}catch(err){
    const existing = MAIN_WORKBENCH_EXPORT_STATE[module] || [];
    updateMainExportReview(module, existing.concat([\`Download status: failed\`, \`Error: \${err.message}\`]));
    if(err.isBlocked) {
      setMainWorkbenchStatus(module, \`Export blocked\`, "warn");
      toast("Export blocked. See review panel.", "warn");
    } else {
      setMainWorkbenchStatus(module, \`Export failed: \${err.message}\`, "error");
      toast("Export failed: " + err.message, "error");
    }
  }`;

html = html.replace(oldExportFileCatch, newExportFileCatch);

fs.writeFileSync('frontend/index.html', html);
console.log('Updated frontend/index.html');
