import type {ResearchClustersPayload,ResearchThemeCluster} from "@/types/research-clusters";

/** Isolated contract examples; identities anchor tests, companies are not source evidence. */
export function clusterFixture(date="2026-10-28"):ResearchClustersPayload {
  const clock=`${date}T10:00:00+08:00`;
  const structure:ResearchThemeCluster={id:"research-structure",name:"人形机器人产业链",type:"industry_chain",state:"supported",support_scope:"industry_structure",driver_state:"candidate",driver:"已核产业结构；当日共同驱动待核",
    branches:[{raw_name:"人形机器人",official_identity:{code:"886069.TI",name:"人形机器人",source:"ths_official",basis:"exact_name",kind:"concept",catalog_version:"b".repeat(64),observed_at:clock},relation:"whole",symbols:["000001"],evidence_ids:["publication"]},
      {raw_name:"汽车零部件",official_identity:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:"b".repeat(64),observed_at:clock},relation:"partial",symbols:["000001"],evidence_ids:["publication"]}],
    stocks:[{symbol:"000001",name:"夹具甲",raw_tags:["人形机器人","汽车零部件"],reason:"人形机器人+汽车零部件",source:"ths",change_pct:0}],
    evidence:[{id:"publication",source:"结构公开说明",url:"https://example.com/structure",published_at:"2026-03-02",observed_at:clock,excerpt:"仅支持产业结构，不证明公司订单或当日共同催化。",evidence_type:"published_industry_structure"}],
    missing:["event_to_company_driver_not_verified"],counter_evidence:["汽车零部件整体不等同于人形机器人"],unique_stock_count:1,change_pct:null};
  const narrative:ResearchThemeCluster={id:"research-sheep",name:"“羊”字辈",type:"market_narrative",state:"candidate",support_scope:"market_narrative",driver_state:"candidate",driver:"来源交易标签；共同催化和完整成员待核",
    branches:[{raw_name:"“羊”字辈",official_identity:null,relation:"candidate",symbols:["000002"],evidence_ids:["reason"]}],
    stocks:[{symbol:"000002",name:null,raw_tags:["“羊”字辈","其他原始标签"],reason:"“羊”字辈+其他原始标签",source:"ths",change_pct:-1}],
    evidence:[{id:"reason",source:"ths",url:null,published_at:null,observed_at:clock,excerpt:"“羊”字辈+其他原始标签",evidence_type:"source_reason_observation"}],missing:["full_theme_universe_not_observed"],counter_evidence:["只观察涨停样本"],unique_stock_count:1,change_pct:null};
  return {scope:"research_theme_clusters",methodology_version:"fixture-v1",version:"a".repeat(64),observed_at:clock,trade_date:date,reference_mode:"current",state:"partial",clusters:[structure,narrative],coverage:{source_scope:"limit_up_sample",source_count:2,tagged_count:2,unique_stock_count:2,complete_universe:false},missing:["full_theme_universe_not_observed"]};
}
