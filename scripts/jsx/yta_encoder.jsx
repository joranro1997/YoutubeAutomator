/*
 * yta_encoder.jsx — manual-trigger batch encoder (F5 from VS Code).
 *
 * Reads data/tmp/yta_render_jobs.json, opens each .prproj, queues it to
 * Adobe Media Encoder via app.encoder.encodeSequence, then calls
 * startBatch(). AME renders async; this script exits in seconds. Premiere
 * may be closed afterwards; AME keeps going. Python polls for the MP4s.
 *
 * Why manual trigger? Premiere 2020 has no CLI flag to run a .jsx, the
 * Startup Scripts mechanism doesn't fire reliably, and QE DOM crashes.
 * One F5 in VS Code per batch is the most robust trigger we have.
 *
 * NEVER hand-edit; managed by `yta render-video|batch --auto-render`.
 */
(function () {
    // Auto-derive the repo root from THIS script's own location
    // (REPO/scripts/jsx/yta_encoder.jsx -> 3 parents = REPO). Portable to
    // any clone path / username. Falls back to a literal only if $.fileName
    // is unavailable.
    var REPO;
    try {
        REPO = File($.fileName).parent.parent.parent.fsName.replace(/\\/g, "/");
    } catch (e) {
        REPO = "C:/Users/Usuario/Downloads/YoutubeAutomator/YoutubeAutomator";
    }
    var QUEUE = REPO + "/data/tmp/yta_render_jobs.json";
    var LOGP  = REPO + "/data/tmp/yta_encoder.log";

    var LOG = [];
    function log(m) { LOG.push((new Date()).toString() + "  " + m); }
    function dump() {
        var f = new File(LOGP); f.encoding = "UTF-8"; f.open("w");
        f.write(LOG.join("\n")); f.close();
    }
    function safe(fn, fb) { try { return fn(); } catch (e) { log("ERR: " + e); return fb; } }

    var qf = new File(QUEUE);
    if (!qf.exists) {
        alert("No queue at " + QUEUE +
              "\n\nRun `yta render-video|batch --auto-render` first.");
        return;
    }
    qf.encoding = "UTF-8"; qf.open("r"); var content = qf.read(); qf.close();
    var jobs = safe(function () { return eval("(" + content + ")"); }, null);
    if (!jobs || !jobs.length) {
        safe(function () { qf.remove(); });
        alert("Queue is empty.");
        return;
    }

    log("yta_encoder triggered with " + jobs.length + " job(s)");
    safe(function () { app.encoder.launchEncoder(); });   // boot AME if needed

    function norm(p) { return String(p).toLowerCase().replace(/\\/g, "/"); }
    function waitForProject(path, timeoutMs) {
        var t0 = (new Date()).getTime();
        while ((new Date()).getTime() - t0 < timeoutMs) {
            var cur = safe(function () { return app.project.path; }, "");
            if (cur && norm(cur) === norm(path)) return true;
            $.sleep(400);
        }
        return false;
    }

    var queued = 0;
    for (var i = 0; i < jobs.length; i++) {
        var j = jobs[i];
        log("[" + (i + 1) + "/" + jobs.length + "] open " + j.project);
        var ok = safe(function () { return app.openDocument(j.project); }, false);
        if (!ok) { log("  openDocument failed"); continue; }

        // openDocument returns BEFORE Premiere is fully ready (esp. with
        // many MasterClips to conform). Poll for the project path to match,
        // then give media indexing a few extra seconds. Without this, AME
        // silently drops the job.
        var ready = waitForProject(j.project, 30000);
        log("  project ready: " + ready);
        safe(function () { $.sleep(5000); });    // extra time for media indexing

        var seq = safe(function () { return app.project.activeSequence; });
        if (!seq || (j.sequence && String(seq.name) !== String(j.sequence))) {
            seq = safe(function () {
                for (var k = 0; k < app.project.sequences.numSequences; k++) {
                    var s = app.project.sequences[k];
                    if (String(s.name) === String(j.sequence)) return s;
                }
                return null;
            });
        }
        if (!seq) { log("  no sequence " + j.sequence); continue; }

        // encodeSequence on Windows needs NATIVE paths (backslashes); the
        // queue JSON uses forward slashes so eval() doesn't choke. Convert
        // via File.fsName -- that's the difference vs the spike that worked.
        var outNative = (new File(j.output)).fsName;
        var presetNative = (new File(j.preset)).fsName;
        var jid = safe(function () {
            return app.encoder.encodeSequence(seq, outNative, presetNative, 0, 1);
        }, null);
        log("  queued jobID=" + jid + " -> " + outNative);
        if (jid) { queued++; }

        // Give AME a moment to ingest the sequence BEFORE we touch the
        // project (closing too early loses the job silently on 2020).
        safe(function () { $.sleep(2000); });

        // For multi-project batches we must close to switch projects.
        // For the LAST job we leave it open so AME doesn't lose context
        // while it finishes ingesting / while startBatch fires.
        if (i < jobs.length - 1) {
            safe(function () { app.project.closeDocument(0, 0); });
        }
    }

    safe(function () { $.sleep(1500); });          // let AME finish ingesting
    safe(function () { app.encoder.startBatch(); });
    safe(function () { $.sleep(1000); });          // and let the batch actually start
    safe(function () { qf.remove(); });            // signal Python: handed off
    log("queued " + queued + " job(s) in AME and started batch");
    dump();
    alert(
        queued + " job(s) queued in AME and started.\n\n" +
        "AME renders asynchronously -- Premiere can be closed.\n" +
        "Log: " + LOGP
    );
})();
