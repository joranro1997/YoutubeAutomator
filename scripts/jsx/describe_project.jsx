/*
 * describe_project.jsx — Phase 3 reconnaissance script for Adobe Premiere Pro.
 *
 * Run this once per template (lom.prproj, loe.prproj) to dump the active
 * sequence's structure to disk. The output JSON drives the design of the
 * production .jsx generator (no XML parsing required).
 *
 * How to run:
 *   1. Open the .prproj in Premiere.
 *   2. File -> Scripts -> Run Script File... -> pick this file.
 *   3. On success an alert tells you where the JSON was written.
 *
 * The output goes to:
 *   <repo>/data/tmp/<projname>_describe.json
 *
 * ExtendScript engine is ES3 — we cannot use let/const/arrow/JSON natively
 * on older Premiere builds, so we ship a minimal stringify and stick to ES3.
 */

(function () {
    // Auto-derive repo root from this script's location (portable).
    var REPO_ROOT;
    try {
        REPO_ROOT = File($.fileName).parent.parent.parent.fsName.replace(/\\/g, "/");
    } catch (e) {
        REPO_ROOT = "C:/Users/Usuario/Downloads/YoutubeAutomator/YoutubeAutomator";
    }

    // --- Minimal JSON.stringify (ES3-safe; no parse) ----------------------- //
    function quote(s) {
        s = String(s);
        var out = '"';
        for (var i = 0; i < s.length; i++) {
            var c = s.charAt(i);
            var code = s.charCodeAt(i);
            if (c === '"') out += '\\"';
            else if (c === '\\') out += '\\\\';
            else if (c === '\n') out += '\\n';
            else if (c === '\r') out += '\\r';
            else if (c === '\t') out += '\\t';
            else if (code < 32) out += '\\u' + ('0000' + code.toString(16)).slice(-4);
            else out += c;
        }
        return out + '"';
    }
    function stringify(v, indent, depth) {
        depth = depth || 0;
        indent = indent || "  ";
        var pad = ""; for (var p = 0; p < depth; p++) pad += indent;
        var padInner = pad + indent;
        if (v === null || v === undefined) return "null";
        var t = typeof v;
        if (t === "boolean") return v ? "true" : "false";
        if (t === "number") return isFinite(v) ? String(v) : "null";
        if (t === "string") return quote(v);
        if (v instanceof Array) {
            if (v.length === 0) return "[]";
            var parts = [];
            for (var k = 0; k < v.length; k++) parts.push(padInner + stringify(v[k], indent, depth + 1));
            return "[\n" + parts.join(",\n") + "\n" + pad + "]";
        }
        if (t === "object") {
            var keys = [];
            for (var key in v) { if (v.hasOwnProperty(key)) keys.push(key); }
            if (keys.length === 0) return "{}";
            var out = [];
            for (var j = 0; j < keys.length; j++) {
                out.push(padInner + quote(keys[j]) + ": " + stringify(v[keys[j]], indent, depth + 1));
            }
            return "{\n" + out.join(",\n") + "\n" + pad + "}";
        }
        return "null";
    }

    // --- Helpers ----------------------------------------------------------- //
    function safe(fn, fallback) {
        try { var r = fn(); return r === undefined ? (fallback === undefined ? null : fallback) : r; }
        catch (e) { return fallback === undefined ? null : fallback; }
    }
    function secOf(t) {
        if (t === null || t === undefined) return null;
        return safe(function () { return Number(t.seconds); }, null);
    }
    function ticksOf(t) {
        if (t === null || t === undefined) return null;
        return safe(function () { return String(t.ticks); }, null);
    }

    function projectItemSummary(pi) {
        if (!pi) return null;
        return {
            name: safe(function () { return pi.name; }),
            nodeId: safe(function () { return pi.nodeId; }),
            type: safe(function () { return pi.type; }),
            mediaPath: safe(function () { return pi.getMediaPath(); }),
            treePath: safe(function () { return pi.treePath; })
        };
    }

    function componentSummary(c) {
        var props = [];
        try {
            for (var i = 0; i < c.properties.numItems; i++) {
                var p = c.properties[i];
                props.push({
                    displayName: safe(function () { return p.displayName; }),
                    matchName: safe(function () { return p.matchName; }),
                    isKeyframed: safe(function () { return p.isTimeVarying(); }, false)
                });
            }
        } catch (e) {}
        return {
            displayName: safe(function () { return c.displayName; }),
            matchName: safe(function () { return c.matchName; }),
            property_count: props.length,
            properties: props
        };
    }

    function clipSummary(clip) {
        var comps = [];
        try {
            for (var i = 0; i < clip.components.numItems; i++) {
                comps.push(componentSummary(clip.components[i]));
            }
        } catch (e) {}
        return {
            name: safe(function () { return clip.name; }),
            nodeId: safe(function () { return clip.nodeId; }),
            type: safe(function () { return clip.type; }),
            mediaType: safe(function () { return clip.mediaType; }),
            start_sec: secOf(safe(function () { return clip.start; })),
            end_sec: secOf(safe(function () { return clip.end; })),
            duration_sec: secOf(safe(function () { return clip.duration; })),
            inPoint_sec: secOf(safe(function () { return clip.inPoint; })),
            outPoint_sec: secOf(safe(function () { return clip.outPoint; })),
            start_ticks: ticksOf(safe(function () { return clip.start; })),
            end_ticks: ticksOf(safe(function () { return clip.end; })),
            speed: safe(function () { return clip.getSpeed(); }),
            disabled: safe(function () { return clip.disabled; }),
            projectItem: projectItemSummary(safe(function () { return clip.projectItem; })),
            components: comps
        };
    }

    function trackSummary(track, index, kind) {
        var clips = [];
        try {
            for (var i = 0; i < track.clips.numItems; i++) {
                clips.push(clipSummary(track.clips[i]));
            }
        } catch (e) {}
        return {
            index: index,
            label: (kind === "video" ? "V" : "A") + (index + 1),
            name: safe(function () { return track.name; }),
            id: safe(function () { return track.id; }),
            mediaType: safe(function () { return track.mediaType; }),
            isMuted: safe(function () { return track.isMuted(); }),
            isLocked: safe(function () { return track.isLocked(); }, null),
            isTargeted: safe(function () { return track.isTargeted(); }, null),
            clip_count: clips.length,
            clips: clips
        };
    }

    // --- Main -------------------------------------------------------------- //
    var seq = app.project.activeSequence;
    if (!seq) {
        alert("No active sequence. Open the .prproj in Premiere and try again.");
        return;
    }

    var videoTracks = [];
    var audioTracks = [];
    try {
        for (var i = 0; i < seq.videoTracks.numTracks; i++) {
            videoTracks.push(trackSummary(seq.videoTracks[i], i, "video"));
        }
    } catch (e) {}
    try {
        for (var j = 0; j < seq.audioTracks.numTracks; j++) {
            audioTracks.push(trackSummary(seq.audioTracks[j], j, "audio"));
        }
    } catch (e) {}

    var markers = [];
    try {
        var mks = seq.markers;
        var first = mks.getFirstMarker();
        var current = first;
        while (current) {
            markers.push({
                name: safe(function () { return current.name; }),
                comments: safe(function () { return current.comments; }),
                start_sec: secOf(safe(function () { return current.start; })),
                end_sec: secOf(safe(function () { return current.end; })),
                type: safe(function () { return current.type; })
            });
            var nxt = mks.getNextMarker(current);
            if (!nxt || nxt === current) break;
            current = nxt;
        }
    } catch (e) {
        // Older Premiere builds expose markers as an array-like with numMarkers.
        try {
            for (var k = 0; k < seq.markers.numMarkers; k++) {
                var m = seq.markers[k];
                markers.push({
                    name: safe(function () { return m.name; }),
                    comments: safe(function () { return m.comments; }),
                    start_sec: secOf(safe(function () { return m.start; })),
                    end_sec: secOf(safe(function () { return m.end; })),
                    type: safe(function () { return m.type; })
                });
            }
        } catch (e2) {}
    }

    var summary = {
        project: {
            name: safe(function () { return app.project.name; }),
            path: safe(function () { return app.project.path; }),
            premiereVersion: safe(function () { return app.version; })
        },
        sequence: {
            name: safe(function () { return seq.name; }),
            sequenceID: safe(function () { return seq.sequenceID; }),
            end_sec: secOf(safe(function () { return seq.end; })),
            zeroPoint_sec: secOf(safe(function () { return seq.zeroPoint; })),
            frame_width: safe(function () { return seq.frameSizeHorizontal; }),
            frame_height: safe(function () { return seq.frameSizeVertical; }),
            videoFrameRate_sec: secOf(safe(function () { return seq.videoFrameRate; })),
            videoTrackCount: videoTracks.length,
            audioTrackCount: audioTracks.length
        },
        video_tracks: videoTracks,
        audio_tracks: audioTracks,
        markers: markers
    };

    var pname = String(safe(function () { return app.project.name; }, "project"));
    pname = pname.replace(/\.prproj$/i, "").replace(/[^A-Za-z0-9_.-]/g, "_");
    var outDir = REPO_ROOT + "/data/tmp";
    var outFolder = new Folder(outDir);
    if (!outFolder.exists) outFolder.create();
    var outPath = outDir + "/" + pname + "_describe.json";

    var f = new File(outPath);
    f.encoding = "UTF-8";
    f.open("w");
    f.write(stringify(summary));
    f.close();

    alert(
        "Phase 3 recon dump complete.\n\n" +
        "Sequence: " + summary.sequence.name + "\n" +
        "Video tracks: " + videoTracks.length + "\n" +
        "Audio tracks: " + audioTracks.length + "\n" +
        "Markers: " + markers.length + "\n\n" +
        "Written to:\n" + outPath
    );
})();
