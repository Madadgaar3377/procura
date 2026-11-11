const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createRequire}=require('node:module');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');

const fixture={
 disciplines:[
  {id:'Civil',name_en:'Civil',name_ar:'مدني',category_count:2,material_count:7},
  {id:'Electrical',name_en:'Electrical',name_ar:'كهربائي',category_count:1,material_count:1},
  {id:'Mechanical',name_en:'Mechanical',name_ar:'ميكانيكي',category_count:0,material_count:0},
 ],
 categories:[{
  id:'metalwork-sub',name_en:'Architectural Metalwork',name_ar:'الأعمال المعدنية',
  main_category_id:'architectural',main_category:'Architectural Specialties',main_category_ar:'الأعمال المعمارية',
  subcategory_id:'metalwork-sub',subcategory:'Architectural Metalwork',subcategory_ar:'الأعمال المعدنية',
  material_count:7,materials:Array.from({length:5},(_,index)=>({
   id:`material-${index}`,name_en:['Gates and louvers','Handrails','Steel frames','Miscellaneous steelwork','Access panels'][index],
   name_ar:'مادة '+index,description:'Real Material Master description '+index,description_ar:'وصف المادة '+index,unit:index?'PCS':'M',
  })),
 }],total:1,page:1,limit:9,
};

function render(arabic=false){
 const filename=path.resolve(__dirname,'../features/marketing/industry-catalog.tsx');
 const localRequire=createRequire(filename);const exported={};
 const source=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const Icon=({children})=>React.createElement('span',null,children);
 const dependencies=name=>name==='@/lib/locale'?{useLocale:()=>({arabic})}
  :name==='@/lib/api/use-api'?{useApi:path=>path.includes('industry-catalog')?{data:fixture,loading:false,reload(){}}:{data:[],loading:false,reload(){}}}
  :name==='next/link'?{default:({href,children,...props})=>React.createElement('a',{href,...props},children),__esModule:true}
  :name==='@tabler/icons-react'?{IconArrowRight:Icon,IconBolt:Icon,IconBuilding:Icon,IconCheck:Icon,IconSettings:Icon}
  :name==='./chrome'?{MarketingCTA:({children})=>React.createElement('a',{href:'/register?role=buyer'},children)}:localRequire(name);
 vm.runInNewContext(source,{exports:exported,require:dependencies,URLSearchParams});
 return renderToStaticMarkup(React.createElement(exported.IndustryCatalog));
}

test('industry catalog groups Material Master previews and links the selected supplier category',()=>{
 const html=render();
 assert.match(html,/Architectural Specialties/);
 assert.match(html,/Architectural Metalwork/);
 assert.match(html,/Gates and louvers/);
 assert.match(html,/Real Material Master description 0/);
 assert.match(html,/>M<\/small>/);
 assert.match(html,/\+2 more materials/);
 assert.match(html,/href="\/suppliers\?main_category_id=architectural&amp;lang=en&amp;subcategory_id=metalwork-sub"/);
 assert.match(html,/Explore Suppliers/);
 assert.match(html,/aria-pressed="true"/);
});

test('Arabic catalog uses localized category and material names and search controls',()=>{
 const html=render(true);
 assert.match(html,/الأعمال المعمارية/);
 assert.match(html,/مادة 0/);
 assert.match(html,/وصف المادة 0/);
 assert.match(html,/type="search"/);
 assert.match(html,/استكشف الموردين/);
});
