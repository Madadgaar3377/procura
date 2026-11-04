"use client";
import Link from 'next/link';
import { ErrorState } from '@/features/shared/loading';
export default function ErrorPage({retry}:{error:Error;retry:()=>void}){return <main className="container public-content"><Link className="brand" href="/">Procura</Link><h1>Page unavailable</h1><ErrorState error={new Error('This page could not be displayed. Please try again.')} retry={retry}/></main>;}
