/* Build-time only: npm install echarts@6.0.0 esbuild@0.25.12 in a separate folder.
   node tools/build_report_charts.cjs <that folder>
   Production uses the checked-in bundle and never runs npm. */
const path=require('path'),fs=require('fs');
const base=path.resolve(process.argv[2]||'.'),resolve=name=>require.resolve(name,{paths:[base]});
const echarts=require(resolve('echarts/package.json')),esbuild=require(resolve('esbuild'));
if(echarts.version!=='6.0.0'||esbuild.version!=='0.25.12')throw Error('Use pinned echarts@6.0.0 and esbuild@0.25.12');
const assets=path.resolve(__dirname,'../analysis/repertoire/assets');
esbuild.buildSync({entryPoints:[path.join(assets,'charts-entry.js')],outfile:path.join(assets,'charts.min.js'),bundle:true,minify:true,format:'iife',target:['es2020'],nodePaths:[path.join(base,'node_modules')],legalComments:'eof',banner:{js:'/* Apache ECharts 6.0.0; Apache-2.0. See ECHARTS-LICENSE.txt. */'}});
fs.copyFileSync(path.join(path.dirname(resolve('echarts/package.json')),'LICENSE'),path.join(assets,'ECHARTS-LICENSE.txt'));
const notice=path.join(path.dirname(resolve('echarts/package.json')),'NOTICE');if(fs.existsSync(notice))fs.copyFileSync(notice,path.join(assets,'ECHARTS-NOTICE.txt'));
const zrender=path.dirname(resolve('zrender/package.json'));fs.copyFileSync(path.join(zrender,'LICENSE'),path.join(assets,'ZRENDER-LICENSE.txt'));
console.log('Offline chart runtime',fs.statSync(path.join(assets,'charts.min.js')).size,'bytes');
