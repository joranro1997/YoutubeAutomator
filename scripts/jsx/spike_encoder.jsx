/*
 * spike_encoder.jsx — go/no-go: can we render headlessly on Premiere 2020?
 *
 * app.encoder is the Adobe Media Encoder BRIDGE — a different subsystem
 * from the QE DOM that crashed. It does NOT walk the timeline DOM, so it
 * may be stable. This spike queues the active sequence to AME and starts
 * the batch. If AME renders the MP4 without crashing Premiere, full
 * auto-render is viable; otherwise we fall back to manual export + the
 * Python auto-upload watcher.
 *
 * ONE-TIME SETUP (required — AME needs an export preset):
 *   In Premiere: File > Export > Media... set H.264, 1920x1080, your usual
 *   YouTube settings, click the "Save Preset" disk icon, name it, OK.
 *   Then find the .epr it created (Export dialog preset dropdown -> the
 *   gear/Import-Export Presets -> "Export Presets..."), save it somewhere
 *   stable and put its FULL path in PRESET below.
 *
 * Run with data/outputs/lom/guideline/guideline.prproj OPEN, via the
 * ExtendScript Debugger. Output MP4 lands next to the project.
 */
(function () {
    var REPO = "C:/Users/Usuario/Downloads/YoutubeAutomator/YoutubeAutomator";
    // User preset exported from Premiere ("yta_render"), auto-located:
    var PRESET = "C:/Users/Usuario/Documents/Adobe/Adobe Media Encoder/14.0/Presets/yta_render.epr";
    var LOGP = REPO + "/data/tmp/spike_encoder.log";

    var LOG = [];
    function log(m) { LOG.push(String(m)); }
    function dump() {
        var f = new File(LOGP); f.encoding = "UTF-8"; f.open("w");
        f.write(LOG.join("\n")); f.close();
    }
    function safe(fn, fb) { try { return fn(); } catch (e) { log("  ! " + e); return fb; } }

    log("Premiere " + safe(function () { return app.version; }, "?"));
    log("encoder bridge present: " + (typeof app.encoder !== "undefined"));

    var seq = app.project.activeSequence;
    if (!seq) { dump(); alert("No active sequence — open guideline.prproj first."); return; }
    log("sequence: " + seq.name);

    var presetFile = new File(PRESET);
    if (!presetFile.exists) {
        dump();
        alert("Preset not found:\n" + PRESET +
              "\n\nDo the ONE-TIME SETUP in the script header, set PRESET, retry.");
        return;
    }

    var projPath = String(safe(function () { return app.project.path; }, ""));
    var outDir = projPath ? projPath.replace(/[^\/\\]+$/, "") : (REPO + "/data/tmp/");
    var outFile = outDir + String(seq.name).replace(/[^A-Za-z0-9_.-]/g, "_") + ".mp4";
    log("output: " + outFile);

    // Queue in Adobe Media Encoder (async). Premiere returns immediately;
    // AME renders in its own process, so you can keep using Premiere or
    // close it. Best UX for batches.
    safe(function () { app.encoder.launchEncoder(); });
    var jobID = safe(function () {
        // (sequence, outPath, presetPath, workArea=ENCODE_ENTIRE(0),
        //  removeFromQueueUponSuccess=1)
        return app.encoder.encodeSequence(seq, outFile, presetFile.fsName, 0, 1);
    }, null);
    log("encodeSequence jobID: " + jobID);
    safe(function () { app.encoder.startBatch(); });   // tell AME to begin

    dump();
    alert(
        "Encoder spike queued in AME.\n\n" +
        "Job: " + jobID + "\n" +
        "Out: " + outFile + "\n\n" +
        "Watch Adobe Media Encoder -- a job for this sequence should appear\n" +
        "in the Cola panel and render automatically. Premiere stays usable.\n" +
        "Log: " + LOGP
    );
})();
