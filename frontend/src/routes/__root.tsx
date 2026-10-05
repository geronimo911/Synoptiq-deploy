import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Outlet, Link, createRootRouteWithContext, useRouter, HeadContent, Scripts } from "@tanstack/react-router";
import { useEffect, type ReactNode } from "react";
import { AppShell } from "@/features/shared/AppShell";
import { RefreshOnLoad } from "@/features/shared/RefreshOnLoad";
import { Toaster } from "@/components/ui/sonner";
import { Button } from "@/components/ui/button";
import appCss from "../styles.css?url";
import { reportError } from "../lib/error-reporting";
function NotFoundComponent(){return <div className="page-state"><h1>404</h1><h2>Signal not found</h2><p>This coordinate falls outside the current system.</p><Button asChild><Link to="/">Return home</Link></Button></div>}
function ErrorComponent({error,reset}:{error:Error;reset:()=>void}){console.error(error);const router=useRouter();useEffect(()=>{reportError(error,{boundary:"tanstack_root_error_component"})},[error]);const backendUnavailable=/failed to fetch|fetch failed|econnrefused|request failed \(503\)/i.test(error.message);return <div className="page-state" role="alert"><h2>{backendUnavailable?"BACKEND UNAVAILABLE":"Synoptiq couldn't load this view"}</h2><p>{error.message}</p><Button onClick={()=>{void router.invalidate();reset()}}>Try again</Button></div>}
export const Route=createRootRouteWithContext<{queryClient:QueryClient}>()({head:()=>({meta:[{charSet:"utf-8"},{title:"Synoptiq | Adaptive Weather Intelligence"},{name:"viewport",content:"width=device-width, initial-scale=1"},{name:"theme-color",content:"#07131b"}],links:[{rel:"stylesheet",href:appCss},{rel:"preconnect",href:"https://fonts.googleapis.com"},{rel:"preconnect",href:"https://fonts.gstatic.com",crossOrigin:"anonymous"},{rel:"stylesheet",href:"https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Manrope:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;600&display=swap"},{rel:"icon",href:"/favicon.svg",type:"image/svg+xml"}]}),shellComponent:RootShell,component:RootComponent,notFoundComponent:NotFoundComponent,errorComponent:ErrorComponent});
function RootShell({children}:{children:ReactNode}){return <html lang="en" className="dark"><head><HeadContent/></head><body>{children}<Scripts/></body></html>}
function RootComponent(){const {queryClient}=Route.useRouteContext();return <QueryClientProvider client={queryClient}><RefreshOnLoad/><AppShell><Outlet/></AppShell><Toaster/></QueryClientProvider>}
