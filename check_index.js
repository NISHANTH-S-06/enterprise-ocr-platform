const fs=require('fs'); 
const html=fs.readFileSync('frontend/index.html','utf8'); 
const scripts=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m=>m[1]); 
console.log('scripts',scripts.length); 
for(let i=0;i<scripts.length;i++){
    new Function(scripts[i]); 
    console.log('script',i+1,'parse PASS');
} 
const ids=[...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]); 
const dup=ids.filter((id,i)=>ids.indexOf(id)!==i); 
console.log('ids',ids.length,'duplicates',[...new Set(dup)].length,[...new Set(dup)].slice(0,50)); 
if([...new Set(dup)].length) process.exit(1);
