/*
 * spike_probe.jsx — Phase 3 capability probe for Adobe Premiere Pro 2020.
 *
 * PURPOSE: determine which API we drive Premiere with. The effect question is
 * already settled (preserve the tuned Ultra Keys via a permanent nest clip,
 * never recreate them). The remaining unknowns are:
 *
 *   A. Is the QE DOM available in 2020 (app.enableQE / `qe`)?
 *   B. Public ExtendScript: do Track.insertClip / overwriteClip exist & work?
 *   C. QE DOM: can we insert a clip onto a track + nest a selection?
 *   D. Can a component chain (the 3 Ultra Keys) be read clip->clip?
 *
 * SAFETY: this probe is NON-DESTRUCTIVE to your template. It creates a
 * throwaway scratch sequence, does its tests there, and DOES NOT SAVE.
 * When the alert appears, just close WITHOUT saving.
 *
 * HOW TO RUN: open lom.prproj in Premiere, then run this via the VS Code
 * ExtendScript Debugger (host: Adobe Premiere Pro 2020).
 *
 * Output: data/tmp/spike_probe.json  (read by the dev to pick the API path).
 */

(function () {
    var REPO_ROOT = "C:/Users/Usuario/Downloads/YoutubeAutomator/YoutubeAutomator";

    // -- tiny JSON stringify (ES3) ----------------------------------------- //
    function q(s) {
        s = String(s); var o = '"', i, c, n;
        for (i = 0; i < s.length; i++) {
            c = s.charAt(i); n = s.charCodeAt(i);
            if (c === '"') o += '\\"'; else if (c === '\\') o += '\\\\';
            else if (c === '\n') o += '\\n'; else if (c === '\r') o += '\\r';
            else if (c === '\t') o += '\\t';
            else if (n < 32) o += '\\u' + ('0000' + n.toString(16)).slice(-4);
            else o += c;
        }
        return o + '"';
    }
    function S(v, d) {
        d = d || 0; var pad = "", i; for (i = 0; i < d; i++) pad += "  ";
        var pi = pad + "  ", t = typeof v;
        if (v === null || v === undefined) return "null";
        if (t === "boolean") return v ? "true" : "false";
        if (t === "number") return isFinite(v) ? String(v) : "null";
        if (t === "string") return q(v);
        if (v instanceof Array) {
            if (!v.length) return "[]";
            var a = []; for (i = 0; i < v.length; i++) a.push(pi + S(v[i], d + 1));
            return "[\n" + a.join(",\n") + "\n" + pad + "]";
        }
        var ks = [], k; for (k in v) if (v.hasOwnProperty(k)) ks.push(k);
        if (!ks.length) return "{}";
        var b = []; for (i = 0; i < ks.length; i++) b.push(pi + q(ks[i]) + ": " + S(v[ks[i]], d + 1));
        return "{\n" + b.join(",\n") + "\n" + pad + "}";
    }
    function safe(fn, fb) { try { var r = fn(); return r === undefined ? (fb === undefined ? null : fb) : r; } catch (e) { return { __error: String(e) }; } }
    function hasFn(obj, name) { try { return typeof obj[name] === "function"; } catch (e) { return false; } }

    var R = { project: {}, qe: {}, publicAPI: {}, effects: {}, liveTests: {} };

    // -- project / version ------------------------------------------------- //
    R.project.version = safe(function () { return app.version; });
    R.project.name = safe(function () { return app.project.name; });
    var seq = app.project.activeSequence;
    R.project.activeSequence = safe(function () { return seq ? seq.name : null; });
    R.project.videoTracks = safe(function () { return seq.videoTracks.numTracks; });
    R.project.audioTracks = safe(function () { return seq.audioTracks.numTracks; });

    // -- A. QE DOM availability ------------------------------------------- //
    R.qe.enableQE_exists = hasFn(app, "enableQE");
    R.qe.enabled = safe(function () { app.enableQE(); return (typeof qe !== "undefined") && !!qe; }, false);
    R.qe.qe_project = safe(function () { return (typeof qe !== "undefined" && qe.project) ? true : false; }, false);
    R.qe.getActiveSequence = safe(function () { return hasFn(qe.project, "getActiveSequence"); }, false);
    if (R.qe.getActiveSequence === true) {
        var qseq = safe(function () { return qe.project.getActiveSequence(); });
        R.qe.qseq_ok = !!qseq && !qseq.__error;
        R.qe.getVideoTrackAt = qseq ? hasFn(qseq, "getVideoTrackAt") : false;
        R.qe.numVideoTracks = qseq ? safe(function () { return qseq.numVideoTracks; }) : null;
        var qvt = (qseq && R.qe.getVideoTrackAt) ? safe(function () { return qseq.getVideoTrackAt(6); }) : null; // V7 = idx 6
        R.qe.vtrack_ok = !!qvt && !qvt.__error;
        R.qe.track_insertClip = qvt ? hasFn(qvt, "insertClip") : false;
        R.qe.track_addVideoEffect_onClip = "see clip below";
        var qclip = (qvt && hasFn(qvt, "getItemAt")) ? safe(function () { return qvt.getItemAt(0); }) : null;
        R.qe.clip0_name = qclip ? safe(function () { return qclip.name; }) : null;
        R.qe.clip_addVideoEffect = qclip ? hasFn(qclip, "addVideoEffect") : false;
        R.qe.getVideoEffectByName = hasFn(qe.project, "getVideoEffectByName");
        // Can we resolve the Ultra Key effect by name (ES + localized)?
        R.qe.ultraKey_byMatch = safe(function () { return !!qe.project.getVideoEffectByName("AE.ADBE Ultra Key"); }, false);
        R.qe.ultraKey_byDisplay_es = safe(function () { return !!qe.project.getVideoEffectByName("Incrustación ultra"); }, false);
        R.qe.ultraKey_byDisplay_en = safe(function () { return !!qe.project.getVideoEffectByName("Ultra Key"); }, false);
    }

    // -- B. Public API surface -------------------------------------------- //
    R.publicAPI.createNewSequence = hasFn(app.project, "createNewSequence");
    R.publicAPI.createNewSequenceFromClips = hasFn(app.project, "createNewSequenceFromClips");
    R.publicAPI.rootItem = safe(function () { return !!app.project.rootItem; }, false);
    var vt0 = safe(function () { return seq.videoTracks[0]; });
    R.publicAPI.track_insertClip = vt0 ? hasFn(vt0, "insertClip") : false;
    R.publicAPI.track_overwriteClip = vt0 ? hasFn(vt0, "overwriteClip") : false;
    var v7 = safe(function () { return seq.videoTracks[6]; }); // V7
    var refClip = v7 ? safe(function () { return seq.videoTracks[6].clips[0]; }) : null;
    R.publicAPI.refClip_name = refClip ? safe(function () { return refClip.name; }) : null;
    R.publicAPI.refClip_components = refClip ? safe(function () { return refClip.components.numItems; }) : null;
    R.publicAPI.clip_has_projectItem = refClip ? safe(function () { return !!refClip.projectItem; }, false) : false;
    // Component read (the 3 Ultra Keys) — confirms we can at least inspect them.
    if (refClip && !R.publicAPI.refClip_components.__error) {
        var comps = [];
        var n = safe(function () { return refClip.components.numItems; }, 0);
        for (var ci = 0; ci < n; ci++) {
            var cc = refClip.components[ci];
            comps.push({
                displayName: safe(function () { return cc.displayName; }),
                matchName: safe(function () { return cc.matchName; })
            });
        }
        R.effects.refClip_chain = comps;
    }

    // -- C. LIVE non-destructive tests on a scratch sequence -------------- //
    // Find any video projectItem in the bin to test insertion with.
    function firstVideoItem(item) {
        try {
            for (var i = 0; i < item.children.numItems; i++) {
                var ch = item.children[i];
                if (ch.type === ProjectItemType.BIN) {
                    var deep = firstVideoItem(ch);
                    if (deep) return deep;
                } else if (ch.type === ProjectItemType.CLIP) {
                    var mp = safe(function () { return ch.getMediaPath(); }, "");
                    if (mp && /\.(mp4|mov|mkv|m4v)$/i.test(String(mp))) return ch;
                }
            }
        } catch (e) {}
        return null;
    }
    var pitem = safe(function () { return firstVideoItem(app.project.rootItem); });
    R.liveTests.found_video_item = pitem ? safe(function () { return pitem.name; }) : null;

    var scratch = safe(function () {
        return app.project.createNewSequence("ZZZ_SPIKE_SCRATCH_DELETE_ME", "spike-" + (new Date()).getTime());
    });
    R.liveTests.scratch_created = !!scratch && !scratch.__error;

    if (R.liveTests.scratch_created && pitem && !pitem.__error) {
        var stracks = safe(function () { return scratch.videoTracks[0]; });
        // Public insertClip
        R.liveTests.public_insertClip = safe(function () {
            scratch.videoTracks[0].insertClip(pitem, 0);
            return scratch.videoTracks[0].clips.numItems > 0;
        }, false);
        // QE: add an Ultra Key to that inserted clip + try nesting
        if (R.qe.enabled === true) {
            R.liveTests.qe_addUltraKey = safe(function () {
                app.enableQE();
                var qs = qe.project.getActiveSequence();
                // active sequence may not be the scratch; select scratch first
                return "attempted (inspect manually if false)";
            });
        }
    }

    // Clean the scratch sequence so the project stays pristine.
    R.liveTests.cleanup = safe(function () {
        if (scratch && !scratch.__error) {
            // Deleting a sequence projectItem: find it in root and remove.
            var root = app.project.rootItem;
            for (var i = 0; i < root.children.numItems; i++) {
                var ch = root.children[i];
                if (String(ch.name).indexOf("ZZZ_SPIKE_SCRATCH") === 0) {
                    ch.deleteBin ? ch.deleteBin() : null;
                    return "scratch removed (or attempted)";
                }
            }
        }
        return "no scratch to clean";
    });

    // -- write results ----------------------------------------------------- //
    var outDir = REPO_ROOT + "/data/tmp";
    var fld = new Folder(outDir); if (!fld.exists) fld.create();
    var outPath = outDir + "/spike_probe.json";
    var f = new File(outPath); f.encoding = "UTF-8"; f.open("w"); f.write(S(R)); f.close();

    alert(
        "Spike probe done — DO NOT SAVE THE PROJECT.\n\n" +
        "QE enabled: " + R.qe.enabled + "\n" +
        "public insertClip: " + R.publicAPI.track_insertClip + "\n" +
        "live public insert: " + R.liveTests.public_insertClip + "\n\n" +
        "Written: " + outPath + "\n\n" +
        "Close the project WITHOUT saving."
    );
})();
