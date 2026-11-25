from pathlib import Path

p = Path('frontend/features/admin/workspace.tsx')
s = p.read_text(encoding='utf-8')
s = s.replace('import {AdminModal} from "@/features/shared/admin-modal";',
              'import {AdminModal} from "@/features/shared/admin-modal";\nimport {SupplierCompanyView,SupplierCapabilityView} from "./supplier-inspection";')
s = s.replace("const [selected,setSelected]=useState('');",
              "const [selected,setSelected]=useState('');const [supplierView,setSupplierView]=useState<'company'|'capability'>('company');")
s = s.replace("setError('');setSelected(String(r.id));}}>{t('View details')}",
              "setError('');setSupplierView('company');setSelected(String(r.id));}}>{t('View details')}")
s = s.replace("onClick={()=>setSelected(String(r.id))}>{t('Inspect capabilities')}",
              "onClick={()=>{setSupplierView('capability');setSelected(String(r.id));}}>{t('Inspect capabilities')}")
a = s.index('{adminDetailOpen&&<AdminModal')
b = s.index('{reviewOpen&&selected&&<AdminModal', a)
replacement = '''{adminDetailOpen&&!reviewOpen&&<AdminModal open className="live-dialog supplier-detail-dialog" size="xl" aria-labelledby="supplier-detail-heading" onRequestClose={()=>setSelected('')}><div className="live-form"><div className="panel-title"><h2 id="supplier-detail-heading">{supplierView==='company'?t('Supplier details'):t('Inspect capabilities')} · {String(company?.name||'')}</h2><Button variant="outline" onClick={()=>setSelected('')}>{t('Close')}</Button></div><State {...detail} retry={detail.reload}/>{supplierDetail&&(supplierView==='company'?<SupplierCompanyView detail={supplierDetail}/>:<SupplierCapabilityView detail={supplierDetail}/>)}</div></AdminModal>}'''
s = s[:a] + replacement + s[b:]
p.write_text(s, encoding='utf-8')
