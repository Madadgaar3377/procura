"use client";
import Link from 'next/link';

export default function ErrorPage({retry}:{error:Error;retry:()=>void}){
  return <main style={{maxWidth:720,margin:'64px auto',padding:'0 24px',fontFamily:'Arial,Helvetica,sans-serif',color:'#0f172a'}}>
    <Link href="/" style={{fontWeight:600,fontSize:22,textDecoration:'none',color:'#0f172a'}}>Procumaterial</Link>
    <h1 style={{margin:'24px 0 12px',fontSize:28}}>Page unavailable</h1>
    <p style={{color:'#475569',marginBottom:20}}>This page could not be displayed. Please try again.</p>
    <button type="button" onClick={retry} style={{padding:'10px 18px',background:'#2563eb',color:'#fff',border:0,borderRadius:6,cursor:'pointer'}}>Try again</button>
  </main>;
}
