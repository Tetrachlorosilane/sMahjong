// 用**上游 mjai-reviewer 的真解析器**（convlog）验我们导出的牌谱：
//   ① tenhou::Log::from_json_str —— 反序列化 + 规则推导（网页版「自定义 JSON」走的就是这里）
//   ② tenhou_to_mjai —— 转成 mjai 事件流（引擎拿到的就是它）
//   ③ 顺手把「结果对的差分」与「每局点数板」串一遍：mjai 事件里的 deltas 必须与点数板对得上。
//
// 用法：tenhou-probe.exe <牌谱.json> [...]
use convlog::{tenhou, tenhou_to_mjai};
use std::fs;

fn main() {
    let mut bad = 0usize;
    for path in std::env::args().skip(1) {
        match probe(&path) {
            Ok(lines) => {
                println!("[ok]   {path}");
                for l in lines {
                    println!("        {l}");
                }
            }
            Err(e) => {
                println!("[FAIL] {path}: {e}");
                bad += 1;
            }
        }
    }
    println!(
        "{}",
        if bad == 0 {
            "REAL-PARSER PASS".to_string()
        } else {
            format!("REAL-PARSER FAIL（{bad} 个文件）")
        }
    );
    std::process::exit(if bad == 0 { 0 } else { 1 });
}

fn probe(path: &str) -> Result<Vec<String>, String> {
    let s = fs::read_to_string(path).map_err(|e| format!("读文件失败：{e}"))?;
    let log = tenhou::Log::from_json_str(&s).map_err(|e| format!("解析失败：{e}"))?;
    let events = tenhou_to_mjai(&log).map_err(|e| format!("转换失败：{e}"))?;

    let mut out = vec![
        format!("局数={} 局制={:?} 有赤={}", log.kyokus.len(), log.game_length, log.has_aka),
        format!("mjai 事件数={}", events.len()),
    ];

    // 每局结算后的四家点数，用来与下一局的点数板对账（mjai 的 start_kyoku.scores 就是点数板）
    let mut prev_after: Option<[i32; 4]> = None;
    for (i, k) in log.kyokus.iter().enumerate() {
        let board = k.scoreboard;
        if let Some(prev) = prev_after {
            if prev != board {
                return Err(format!(
                    "第{}局起始点数板 {board:?} ≠ 上一局结算后的 {prev:?}",
                    i + 1
                ));
            }
        }
        let (tag, deltas) = match &k.end_status {
            tenhou::EndStatus::Hora { details } => {
                let d: Vec<[i32; 4]> = details.iter().map(|x| x.score_deltas).collect();
                ("和了", d)
            }
            tenhou::EndStatus::Ryukyoku { score_deltas } => ("流局", vec![*score_deltas]),
        };
        let mut after = board;
        for d in &deltas {
            for s in 0..4 {
                after[s] += d[s];
            }
        }
        if deltas.len() > 1 {
            out.push(format!("第{}局 {tag}（多响 {} 组）", i + 1, deltas.len()));
        }
        prev_after = Some(after);
    }
    if let Some(last) = prev_after {
        out.push(format!("终局点数 {last:?}（和={}）", last.iter().sum::<i32>()));
    }

    // mjai 流的头部/尾部各看一眼，确认事件形状正常
    if let Some(first) = events.first() {
        out.push(format!(
            "首事件 {}",
            serde_json::to_string(first).unwrap_or_default()
        ));
    }
    if let Some(last) = events.last() {
        out.push(format!(
            "末事件 {}",
            serde_json::to_string(last).unwrap_or_default()
        ));
    }

    // 走一遍**转换后的 mjai 事件流**（引擎与报告真正拿到的东西）：
    // start_kyoku.scores 是点数板，hora/ryukyoku 的 deltas 是收支 —— 两者必须自洽，
    // 否则报告里的分数收支会与点数板对不上。
    let mut scores: Option<[i32; 4]> = None;
    let mut hora = 0usize;
    let mut ryukyoku = 0usize;
    let mut with_deltas = 0usize;
    for ev in &events {
        match ev {
            convlog::Event::StartKyoku { scores: sc, .. } => {
                if let Some(prev) = scores {
                    if prev != *sc {
                        return Err(format!(
                            "mjai 事件流：本局 start_kyoku.scores {sc:?} ≠ 上一局 deltas 结算后的 {prev:?}"
                        ));
                    }
                }
                scores = Some(*sc);
            }
            convlog::Event::Hora { deltas, .. } => {
                hora += 1;
                if let (Some(d), Some(s)) = (deltas, scores) {
                    with_deltas += 1;
                    let mut after = s;
                    for i in 0..4 {
                        after[i] += d[i];
                    }
                    scores = Some(after);
                }
            }
            convlog::Event::Ryukyoku { deltas } => {
                ryukyoku += 1;
                if let (Some(d), Some(s)) = (deltas, scores) {
                    with_deltas += 1;
                    let mut after = s;
                    for i in 0..4 {
                        after[i] += d[i];
                    }
                    scores = Some(after);
                }
            }
            _ => {}
        }
    }
    out.push(format!(
        "mjai 事件流：和了 {hora} 次 / 流局 {ryukyoku} 次（带 deltas 的 {with_deltas} 组），点数板自洽 ✓"
    ));
    Ok(out)
}
