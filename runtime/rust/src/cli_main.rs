//! CLI del validador Rust generado (misma interfaz y salida JSON que el C).
//!
//! Uso: validator <payload|-> [--chunk N] [--max-depth D] [--fail-fast]
//!                [--bench R] [--jsonl]
mod schema;
mod wdpda_rt;

use std::io::{BufRead, Read, Write};
use std::time::Instant;
use wdpda_rt::{Ctx, Status, CODE_NAMES};

fn json_str(out: &mut String, s: &str) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

fn result_json(c: &Ctx, secs: f64) -> String {
    let s = &schema::SCHEMA;
    let fatal = c.status != Status::Valid && c.status != Status::Invalid;
    let mut o = String::new();
    o.push_str(&format!("{{\"status\":\"{}\",\"weight\":", c.status.name()));
    if fatal {
        o.push_str("null");
    } else {
        o.push_str(&c.weight.to_string());
    }
    o.push_str(",\"errors\":[");
    for (i, e) in c.errors[..c.nerrors].iter().enumerate() {
        if i > 0 {
            o.push(',');
        }
        o.push_str(&format!("{{\"code\":\"{}\",\"path\":", CODE_NAMES[e.code as usize]));
        json_str(&mut o, e.path());
        o.push_str(&format!(",\"offset\":{},\"message\":", e.offset));
        json_str(&mut o, e.message());
        o.push('}');
    }
    o.push_str(&format!("],\"error_count\":{}", c.error_count));
    if fatal {
        o.push_str(",\"fatal_message\":");
        json_str(&mut o, c.fatal_message());
        o.push_str(&format!(",\"fatal_offset\":{}", c.fatal_offset));
    }
    let mbs = if secs > 0.0 { c.offset as f64 / secs / 1e6 } else { 0.0 };
    o.push_str(&format!(
        ",\"bytes\":{},\"max_depth\":{},\"lanes_peak\":{},\"tokens\":{},\"seconds\":{:.9},\"mb_s\":{:.2},\
         \"cycles_per_byte\":null,\"runtime_allocations\":0,\"ctx_bytes\":{},\"automaton\":{{\"states\":{},\
         \"transitions\":{},\"nodes\":{},\"dfas\":{}}}}}",
        c.offset, c.max_depth_seen, c.lanes_peak, c.ntokens, secs, mbs,
        std::mem::size_of::<Ctx>(), s.formal_states, s.formal_transitions, s.nodes.len(), s.dfas.len()
    ));
    o
}

fn run(c: &mut Ctx, data: &[u8], chunk: usize, max_depth: u32, fail_fast: bool) {
    let s = &schema::SCHEMA;
    c.init(max_depth, fail_fast);
    for part in data.chunks(chunk.max(1)) {
        if c.feed(s, part) != Status::Running {
            break;
        }
    }
    c.finish(s);
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut file: Option<String> = None;
    let mut chunk = 65536usize;
    let mut max_depth = 0u32;
    let mut fail_fast = false;
    let mut bench = 0usize;
    let mut jsonl = false;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--chunk" => { i += 1; chunk = args[i].parse().unwrap_or(65536); }
            "--max-depth" => { i += 1; max_depth = args[i].parse().unwrap_or(0); }
            "--fail-fast" => fail_fast = true,
            "--bench" => { i += 1; bench = args[i].parse().unwrap_or(0); }
            "--jsonl" => jsonl = true,
            a => file = Some(a.to_string()),
        }
        i += 1;
    }
    let Some(file) = file else {
        eprintln!("uso: validator <payload|-> [--chunk N] [--max-depth D] [--fail-fast] [--bench R] [--jsonl]");
        std::process::exit(1);
    };
    let mut ctx = Ctx::new_boxed(); // una sola vez; feed/finish no asignan
    let reader: Box<dyn Read> = if file == "-" {
        Box::new(std::io::stdin())
    } else {
        Box::new(std::fs::File::open(&file).unwrap_or_else(|e| {
            eprintln!("fopen: {e}");
            std::process::exit(1)
        }))
    };
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    if jsonl {
        let br = std::io::BufReader::new(reader);
        for line in br.split(b'\n') {
            let mut line = line.unwrap_or_default();
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            run(&mut ctx, &line, chunk, max_depth, fail_fast);
            let _ = writeln!(out, "{}", result_json(&ctx, 0.0));
        }
        return;
    }
    let mut data = Vec::new();
    let mut r = reader;
    r.read_to_end(&mut data).expect("lectura");
    let secs;
    if bench > 0 {
        let mut times = Vec::new();
        for k in 0..bench + 2 {
            let t0 = Instant::now();
            run(&mut ctx, &data, chunk, max_depth, fail_fast);
            if k >= 2 {
                times.push(t0.elapsed().as_secs_f64());
            }
        }
        times.sort_by(|a, b| a.partial_cmp(b).unwrap());
        secs = times[times.len() / 2];
    } else {
        let t0 = Instant::now();
        run(&mut ctx, &data, chunk, max_depth, fail_fast);
        secs = t0.elapsed().as_secs_f64();
    }
    let _ = writeln!(out, "{}", result_json(&ctx, secs));
    std::process::exit(match ctx.status {
        Status::Valid => 0,
        Status::Invalid => 2,
        _ => 3,
    });
}
