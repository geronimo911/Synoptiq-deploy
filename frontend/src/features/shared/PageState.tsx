import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
export function PageLoading({ label="Reading forecast signals" }: {label?:string}) { return <div className="page-state"><div className="radar-loader"/><p>{label}…</p></div>; }
export function PageError({ message, retry }: {message:string;retry:()=>void}) { return <div className="page-state"><AlertTriangle className="state-icon"/><h2>Signal unavailable</h2><p>{message}</p><Button onClick={retry}><RefreshCw/>Retry</Button></div>; }
export function EmptyState({ label="No observations match this view." }: {label?:string}) { return <div className="page-state"><p>{label}</p></div>; }
