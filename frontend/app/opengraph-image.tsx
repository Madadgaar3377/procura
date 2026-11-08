import {ImageResponse} from 'next/og';

export const alt='Procumaterial — Connecting Saudi Businesses with Trusted Suppliers.';
export const size={width:1200,height:630};
export const contentType='image/png';

export default function Image(){
  return new ImageResponse(<div style={{display:'flex',flexDirection:'column',width:'100%',height:'100%',background:'#0F172A',color:'#fff',padding:'65px 75px',fontFamily:'sans-serif'}}>
    <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',fontSize:25}}><span>Procumaterial</span><span style={{fontSize:19,color:'#cbd5e1'}}>Saudi B2B Procurement Platform</span></div>
    <div style={{display:'flex',height:2,width:55,background:'#60a5fa',marginTop:68,marginBottom:30}}/>
    <div style={{display:'flex',fontSize:76,lineHeight:1.1,letterSpacing:-3,maxWidth:1000}}>Connecting Saudi Businesses with Trusted Suppliers.</div>
    <div style={{display:'flex',fontSize:24,color:'#cbd5e1',marginTop:'auto',gap:23}}><span>Find suppliers</span><span style={{color:'#60a5fa'}}>→</span><span>Create RFQs</span><span style={{color:'#60a5fa'}}>→</span><span>Compare quotations</span></div>
  </div>,size);
}
