const fs=require('fs');
const html = fs.readFileSync('frontend/index.html', 'utf8');
const inputs = html.match(/<input[^>]*data-erp-name[^>]*>/g);
if(inputs) {
  inputs.slice(0, 10).forEach(i => console.log(i));
} else {
  console.log("no inputs found");
}
