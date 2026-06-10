const fs=require('fs');
const html = fs.readFileSync('frontend/index.html', 'utf8');
const m = html.match(/onclick="export[^>]+/g);
console.log(m ? m.join('\n') : 'none');
