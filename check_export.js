const fs=require('fs');
const html = fs.readFileSync('frontend/index.html', 'utf8');
const idx = html.indexOf('const ext = fmt === "excel" ? "xls');
console.log(html.substring(idx, idx+500));
