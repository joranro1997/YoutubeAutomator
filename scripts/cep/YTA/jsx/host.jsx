/*
 * YTA CEP host.jsx — auto-loaded by Premiere via the panel manifest's
 * <ScriptPath>. Runs once on panel open (== Premiere boot, since the panel
 * is AutoVisible); also exposes ytaRunQueue() as a global that the panel's
 * JS polling calls every ~15s so new queues are picked up WITHOUT having
 * to restart Premiere.
 *
 * queue missing/empty -> silent no-op (normal Premiere session).
 * queue present       -> open each .prproj, queue to Adobe Media Encoder
 *                        via app.encoder.encodeSequence, startBatch, then
 *                        remove the queue file. AME renders asynchronously.
 *
 * Mirrors scripts/jsx/yta_encoder.jsx (the F5 fallback). Keep in sync.
 */

var YTA_REPO  = "C:/Users/Usuario/Downloads/YoutubeAutomator/YoutubeAutomator";
var YTA_QUEUE = YTA_REPO + "/data/tmp/yta_render_jobs.json";
var YTA_LOGP  = YTA_REPO + "/data/tmp/yta_cep.log";
var YTA_BUSY  = false;   // re-entrancy guard for polling

function ytaLog(m) {
    var f = new File(YTA_LOGP); f.encoding = "UTF-8"; f.open("a");
    f.write((new Date()).toString() + "  " + m + "\n"); f.close();
}
function ytaSafe(fn, fb) { try { return fn(); } catch (e) { ytaLog("ERR: " + e); return fb; } }

function _ytaNorm(p) { return String(p).toLowerCase().replace(/\\/g, "/"); }
function _ytaWaitForProject(path, timeoutMs) {
    var t0 = (new Date()).getTime();
    while ((new Date()).getTime() - t0 < timeoutMs) {
        var cur = ytaSafe(function () { return app.project.path; }, "");
        if (cur && _ytaNorm(cur) === _ytaNorm(path)) return true;
        $.sleep(400);
    }
    return false;
}

function _ytaProcessJobs(jobs) {
    ytaLog("CEP worker triggered with " + jobs.length + " job(s)");
    ytaSafe(function () { app.encoder.launchEncoder(); });
    var queued = 0;
    for (var i = 0; i < jobs.length; i++) {
        var j = jobs[i];
        ytaLog("[" + (i + 1) + "/" + jobs.length + "] open " + j.project);
        var ok = ytaSafe(function () { return app.openDocument(j.project); }, false);
        if (!ok) { ytaLog("  openDocument failed"); continue; }

        var ready = _ytaWaitForProject(j.project, 30000);
        ytaLog("  project ready: " + ready);
        ytaSafe(function () { $.sleep(5000); });

        var seq = ytaSafe(function () { return app.project.activeSequence; });
        if (!seq || (j.sequence && String(seq.name) !== String(j.sequence))) {
            seq = ytaSafe(function () {
                for (var k = 0; k < app.project.sequences.numSequences; k++) {
                    var s = app.project.sequences[k];
                    if (String(s.name) === String(j.sequence)) return s;
                }
                return null;
            });
        }
        if (!seq) { ytaLog("  no sequence " + j.sequence); continue; }

        // Native Windows paths -- encodeSequence on 2020 silently drops the
        // job if it sees forward slashes (the queue JSON uses /).
        var outNative = (new File(j.output)).fsName;
        var presetNative = (new File(j.preset)).fsName;
        var jid = ytaSafe(function () {
            return app.encoder.encodeSequence(seq, outNative, presetNative, 0, 1);
        }, null);
        ytaLog("  queued jobID=" + jid + " -> " + outNative);
        if (jid) { queued++; }

        ytaSafe(function () { $.sleep(2000); });
        if (i < jobs.length - 1) {
            ytaSafe(function () { app.project.closeDocument(0, 0); });
        }
    }

    ytaSafe(function () { $.sleep(1500); });
    ytaSafe(function () { app.encoder.startBatch(); });
    ytaSafe(function () { $.sleep(1000); });
    return "queued " + queued + "/" + jobs.length;
}

// Exposed for the panel's setInterval polling.
function ytaRunQueue() {
    if (YTA_BUSY) { return "busy"; }
    var qf = new File(YTA_QUEUE);
    if (!qf.exists) { return "idle"; }
    YTA_BUSY = true;
    try {
        qf.encoding = "UTF-8"; qf.open("r");
        var content = qf.read(); qf.close();
        var jobs = ytaSafe(function () { return eval("(" + content + ")"); }, null);
        if (!jobs || !jobs.length) {
            ytaSafe(function () { qf.remove(); });
            return "empty";
        }
        var status = _ytaProcessJobs(jobs);
        ytaSafe(function () { qf.remove(); });   // signal Python: handed off
        ytaLog(status);
        return status;
    } finally {
        YTA_BUSY = false;
    }
}

// Run once at panel-open in case there's already a queue waiting.
ytaRunQueue();
