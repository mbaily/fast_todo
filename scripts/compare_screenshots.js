const fs = require('fs');
const PNG = require('pngjs').PNG;
const pixelmatch = require('pixelmatch');

function readPng(path){ return new Promise((res,rej)=>{ fs.createReadStream(path).pipe(new PNG()).on('parsed', function(){ res(this); }).on('error', rej); }); }

(async ()=>{
  const a = 'screenshots/check2030.png';
  const b = 'screenshots/real_browser_connected.png';
  const out = 'screenshots/diff.png';
  if(!fs.existsSync(a) || !fs.existsSync(b)){ console.error('Missing files', a, b); process.exit(2); }
  const pa = await readPng(a);
  const pb = await readPng(b);
  const {width, height} = pa;
  if (width !== pb.width || height !== pb.height) {
    console.log('Different sizes:', pa.width,pa.height, pb.width,pb.height);
  }
  const w = Math.max(pa.width, pb.width);
  const h = Math.max(pa.height, pb.height);
  const diff = new PNG({width: w, height: h});
  const count = pixelmatch(pa.data, pb.data, diff.data, w, h, {threshold:0.1});
  diff.pack().pipe(fs.createWriteStream(out));
  console.log('PIXEL_MISMATCH_COUNT:', count);
  console.log('DIFF_SAVED:', out);
})();
