const fs = require('fs');
const html = fs.readFileSync('frontend/index.html', 'utf8');
const regex = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
const scripts = [];
let match;
while ((match = regex.exec(html)) !== null) {
    scripts.push(match[1]);
}
console.log('scripts count:', scripts.length);
for (let i = 0; i < scripts.length; i++) {
    try {
        new Function(scripts[i]);
        console.log('script ' + (i + 1) + ' parse PASS');
    } catch (e) {
        console.log('script ' + (i + 1) + ' FAIL:', e.message);
        process.exit(1);
    }
}
const ids = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);
const dup = ids.filter((id, i) => ids.indexOf(id) !== i);
const uniqueDup = [...new Set(dup)];
console.log('ids count:', ids.length, 'duplicates:', uniqueDup.length);
if (uniqueDup.length > 0) {
    console.log('Duplicate IDs:', uniqueDup.slice(0, 50));
    process.exit(1);
}
console.log('ALL CHECKS PASSED');
