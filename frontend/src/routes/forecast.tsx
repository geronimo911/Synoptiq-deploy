import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { zodValidator } from "@tanstack/zod-adapter";
import { ForecastView } from "@/features/forecast/ForecastView";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { routeHead } from "@/features/shared/RouteMeta";
const searchSchema=z.object({region:z.enum(["KWG","BOB","IGP"]).catch("KWG"),variable:z.enum(["precipitation","temperature","wind_speed"]).catch("precipitation"),lead:z.coerce.number().catch(72)});
export const Route=createFileRoute("/forecast")({validateSearch:zodValidator(searchSchema),head:()=>routeHead("Live Forecast","Inspect Synoptiq source weights, trust, regime, bias correction, bust risk, and scientific provenance."),component:Page});
function Page(){const s=Route.useSearch();const nav=Route.useNavigate();const set=(p:Partial<typeof s>)=>nav({to:".",search:prev=>({...prev,...p})});return <><div className="page"><SectionHeading eyebrow="Operational forecast / latest cycle" title="Live forecast intelligence" copy="One forecast assembled from three model signals, with every decision exposed."/><ForecastView region={s.region} variable={s.variable} lead={s.lead} onRegion={region=>set({region})} onVariable={variable=>set({variable})} onLead={lead=>set({lead})}/></div></>}
