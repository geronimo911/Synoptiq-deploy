import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ReactNode } from "react";
export function Control({ label, children }: {label:string;children:ReactNode}) { return <label className="control"><span>{label}</span>{children}</label>; }
export function SelectControl({ label, value, options, onChange }: {label:string;value:string;options:{value:string;label:string}[];onChange:(value:string)=>void}) { return <Control label={label}><Select value={value} onValueChange={onChange}><SelectTrigger><SelectValue/></SelectTrigger><SelectContent>{options.map((o)=><SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent></Select></Control>; }
