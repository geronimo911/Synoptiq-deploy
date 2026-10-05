import type { BlendResponse, ExtremeGuidanceResponse, Region, ReplayEventDetail, ReplayEventSummary, SourceMeta, VariableMeta, VerificationSummary, WeightMapResponse, RegionCode, VariableName, Season, Regime } from "./types";

export const mockRegions: Region[] = [
  { code: "KWG", name: "Kerala / Western Ghats", lat: 10.5, lon: 76.2, emphasis: "Heavy rainfall / orographic terrain effects", coastal: true },
  { code: "BOB", name: "Bay of Bengal / East Coast", lat: 17.7, lon: 83.3, emphasis: "Coastal systems, monsoon depressions, high wind", coastal: true },
  { code: "IGP", name: "Indo-Gangetic Plains / NW India", lat: 28.6, lon: 77.2, emphasis: "Temperature extremes, western-disturbance regime shifts", coastal: false },
];
export const mockVariables: VariableMeta[] = [
  { variable: "precipitation", unit: "mm/24h", extreme_threshold: 20 },
  { variable: "temperature", unit: "°C", extreme_threshold: 40 },
  { variable: "wind_speed", unit: "m/s", extreme_threshold: 17 },
];
export const mockLeadTimes = [24, 48, 72, 96, 120];
export const mockSources: Record<string, SourceMeta> = {
  GFS: { kind: "physical_nwp", provider: "NOAA NOMADS", role: "Primary baseline source" },
  IFS: { kind: "physical_nwp", provider: "ECMWF Open Data", role: "High-value comparison source" },
  AIFS: { kind: "ai_nwp", provider: "ECMWF Open Data", role: "AI-native comparison source" },
};
const now = "2026-09-23T06:00:00Z";
const values: Record<VariableName, { unit: string; raw: number; corrected: number; sources: number[] }> = {
  precipitation: { unit: "mm/24h", raw: 1.253, corrected: 5.847, sources: [14.512, 0, 1.281] },
  temperature: { unit: "°C", raw: 39.2, corrected: 38.4, sources: [40.1, 37.8, 39.5] },
  wind_speed: { unit: "km/h", raw: 66.9, corrected: 64.4, sources: [79.6, 60.5, 63.0] },
};
export function mockBlend(region: RegionCode, variable: VariableName, lead: number): BlendResponse {
  const v = values[variable];
  const abstain = region === "BOB" && variable === "wind_speed" && lead >= 120;
  return {
    region, region_name: mockRegions.find((r) => r.code === region)?.name ?? region, variable, unit: v.unit, run_time: now,
    valid_time: "2026-09-26T06:00:00Z", lead_hours: lead, season: "sw_monsoon", regime: region === "BOB" ? "depression" : "active_monsoon",
    regime_probs: { active_monsoon: region === "KWG" ? .52 : .16, break_monsoon: .08, western_disturbance: region === "IGP" ? .42 : .05, depression: region === "BOB" ? .54 : .12, normal: region === "IGP" ? .29 : .23 },
    sources: [
      { model: "AIFS", forecast_value: v.sources[0] ?? 0, weight: 0, historical_skill: .3395 },
      { model: "GFS", forecast_value: v.sources[1] ?? 0, weight: .022, historical_skill: .5186 },
      { model: "IFS", forecast_value: v.sources[2] ?? 0, weight: .978, historical_skill: .649 },
    ], disagreement: variable === "precipitation" ? 6.56 : 2.84, raw_blend_value: v.raw, bias_corrected_value: v.corrected, final_value: v.corrected,
    trust: { historical_skill_component: .6461, disagreement_component: abstain ? .18 : .7376, lead_time_component: Math.max(.2, 1 - lead / 168 * .75), regime_stability_component: abstain ? .12 : .64, data_quality_component: 1, trust_score: abstain ? .31 : .5963 },
    bust_probability: abstain ? .78 : .08, bust_flag: abstain, abstain,
    explanation: [
      { feature: "historical_skill_avg", contribution: .184, direction: "increases_trust", detail: "IFS has remained reliable in this region and regime." },
      { feature: "source_disagreement", contribution: -.092, direction: "decreases_trust", detail: "Source spread introduces meaningful uncertainty." },
      { feature: "lead_time", contribution: -.061, direction: "decreases_weight", detail: `Confidence decays at +${lead}h lead.` },
      { feature: "regime_stability", contribution: .073, direction: "increases_weight", detail: "The detected weather regime is comparatively stable." },
    ], fallback_used: false,
    provenance: { AIFS: "ECMWF Open Data", GFS: "NOAA NOMADS", IFS: "ECMWF Open Data", regime_detector: "Auditable rule-based classifier", explanation_backend: "shap.TreeExplainer" },
  };
}
export function mockWeightMap(region: RegionCode, variable: VariableName, season: Season, regime: Regime): WeightMapResponse {
  return { region, variable, season, regime, points: mockLeadTimes.map((lead, i) => {
    const ifs = Math.max(.45, .92 - i * .045); const gfs = .08 + i * .035; const aifs = Math.max(0, 1 - ifs - gfs);
    return { lead_hours: lead, weights: { IFS: +ifs.toFixed(3), GFS: +gfs.toFixed(3), AIFS: +aifs.toFixed(3) }, trust_score: +(.84 - i * .075).toFixed(3) };
  }) };
}
export const mockVerification: VerificationSummary[] = [
  { region:"KWG",variable:"precipitation",metric:"CSI@20mm",threshold:20,best_single_model:"AIFS",best_single_model_score:.75,synoptiq_score:.8889,relative_improvement:.1852,meets_target:true,target_relative_improvement:.05,best_single_model_csi:.75,synoptiq_csi:.8889,relative_csi_improvement:.1852 },
  { region:"BOB",variable:"precipitation",metric:"CSI@20mm",threshold:20,best_single_model:"IFS",best_single_model_score:.62,synoptiq_score:.829,relative_improvement:.337,meets_target:true,target_relative_improvement:.05,best_single_model_csi:.62,synoptiq_csi:.829,relative_csi_improvement:.337 },
  { region:"IGP",variable:"precipitation",metric:"CSI@20mm",threshold:20,best_single_model:"GFS",best_single_model_score:.51,synoptiq_score:.694,relative_improvement:.361,meets_target:true,target_relative_improvement:.05,best_single_model_csi:.51,synoptiq_csi:.694,relative_csi_improvement:.361 },
  { region:"KWG",variable:"temperature",metric:"RMSE",threshold:null,best_single_model:"IFS",best_single_model_score:1.12,synoptiq_score:1.05,relative_improvement:.0625,meets_target:null,target_relative_improvement:null },
  { region:"BOB",variable:"temperature",metric:"RMSE",threshold:null,best_single_model:"AIFS",best_single_model_score:1.34,synoptiq_score:1.38,relative_improvement:-.0299,meets_target:null,target_relative_improvement:null },
  { region:"IGP",variable:"temperature",metric:"RMSE",threshold:null,best_single_model:"AIFS",best_single_model_score:1.28,synoptiq_score:1.19,relative_improvement:.0703,meets_target:null,target_relative_improvement:null },
  { region:"KWG",variable:"wind_speed",metric:"RMSE",threshold:null,best_single_model:"IFS",best_single_model_score:7.0,synoptiq_score:7.3,relative_improvement:-.0429,meets_target:null,target_relative_improvement:null },
  { region:"BOB",variable:"wind_speed",metric:"RMSE",threshold:null,best_single_model:"GFS",best_single_model_score:8.4,synoptiq_score:9.8,relative_improvement:-.1667,meets_target:null,target_relative_improvement:null },
  { region:"IGP",variable:"wind_speed",metric:"RMSE",threshold:null,best_single_model:"AIFS",best_single_model_score:6.2,synoptiq_score:6.0,relative_improvement:.0323,meets_target:null,target_relative_improvement:null },
];
export function mockExtreme(region: RegionCode, lead: number): ExtremeGuidanceResponse { return { region, lead_hours: lead, valid_time: now, guidance: [
  { variable:"precipitation",threshold:20,unit:"mm/24h",probability: region === "KWG" ? .72 : .44,calibrated:true },
  { variable:"temperature",threshold:40,unit:"°C",probability: region === "IGP" ? .81 : .18,calibrated:true },
  { variable:"wind_speed",threshold:62,unit:"km/h",probability: region === "BOB" ? .67 : .23,calibrated:false },
]}; }
export const mockReplayDetails: ReplayEventDetail[] = [
  { event_id:"EVT-BOB-20260503-L120",region:"BOB",variable:"precipitation",unit:"mm/24h",valid_time:"2026-05-03T00:00:00Z",lead_hours:120,label:"Bay of Bengal / East Coast — 03 May 2026",headline:"Synoptiq beat the naive average but not the single best model on this particular case.",raw_sources:[{model:"GFS",forecast_value:306.02,weight:null,historical_skill:null},{model:"IFS",forecast_value:287.83,weight:null,historical_skill:null},{model:"AIFS",forecast_value:213.48,weight:null,historical_skill:null}],naive_average:269.11,single_model_choice:{model:"IFS",forecast_value:287.83},synoptiq_blend:289.49,reference_value:282.54,naive_average_error:13.43,single_model_error:5.29,synoptiq_error:6.95,narrative:["Raw sources disagreed materially: GFS=306.0mm, IFS=287.8mm, AIFS=213.5mm at +120h lead.","The rule-based regime classifier read this as ‘depression’ with 54% context stability.","A naive equal-weight average would have given 269.1mm — 13.4mm from verification.","Always trusting IFS alone would have given 287.8mm — 5.3mm from verification.","Synoptiq’s context-aware blend gave 289.5mm — 7.0mm from verification. It did not win this case, and reports that plainly."] },
  { event_id:"EVT-KWG-20260718-L072",region:"KWG",variable:"precipitation",unit:"mm/24h",valid_time:"2026-07-18T00:00:00Z",lead_hours:72,label:"Kerala / Western Ghats — 18 Jul 2026",headline:"Synoptiq cut the error vs. both the naive average and the default single model.",raw_sources:[{model:"GFS",forecast_value:82.4,weight:.12,historical_skill:.51},{model:"IFS",forecast_value:119.8,weight:.71,historical_skill:.66},{model:"AIFS",forecast_value:103.2,weight:.17,historical_skill:.58}],naive_average:101.8,single_model_choice:{model:"IFS",forecast_value:119.8},synoptiq_blend:112.6,reference_value:110.4,naive_average_error:8.6,single_model_error:9.4,synoptiq_error:2.2,narrative:["The source envelope spanned 37.4mm during an active monsoon episode.","Historical skill and regime context shifted 71% of the blend toward IFS.","The naive average missed the verified total by 8.6mm.","The default single-model choice missed by 9.4mm.","Synoptiq closed to 2.2mm after bias correction — a clear contextual win."] },
  { event_id:"EVT-IGP-20260114-L096",region:"IGP",variable:"temperature",unit:"°C",valid_time:"2026-01-14T00:00:00Z",lead_hours:96,label:"Indo-Gangetic Plains — 14 Jan 2026",headline:"Synoptiq held the lowest error through a western-disturbance regime shift.",raw_sources:[{model:"GFS",forecast_value:13.8,weight:.31,historical_skill:.62},{model:"IFS",forecast_value:11.1,weight:.55,historical_skill:.69},{model:"AIFS",forecast_value:15.2,weight:.14,historical_skill:.57}],naive_average:13.37,single_model_choice:{model:"IFS",forecast_value:11.1},synoptiq_blend:12.4,reference_value:12.1,naive_average_error:1.27,single_model_error:1,synoptiq_error:.3,narrative:["A western disturbance widened the source spread at +96h.","The regime-aware blend stayed centered on the colder IFS signal.","The naive average ran 1.3°C warm.","IFS alone ran 1.0°C cold.","Synoptiq finished 0.3°C from verification."] },
];
export const mockReplaySummaries: ReplayEventSummary[] = mockReplayDetails.map(({ event_id, region, variable, valid_time, label, headline }) => ({ event_id, region, variable, valid_time, label, headline }));
