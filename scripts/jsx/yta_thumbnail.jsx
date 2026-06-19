/*
 * yta_thumbnail.jsx — function injected into Photoshop (run as a launched
 * .jsx or via Application.DoJavaScript). Replaces the 2 text Smart Objects
 * in a thumbnail template with new top + bottom text and exports a PNG.
 *
 * Anti-overflow: long copy used to be CLIPPED by the size of the Smart
 * Object that holds it. We now measure the text against the SO's own canvas
 * and, when it overflows, shrink the font (and re-centre it INSIDE the SO,
 * so the SO's position on the thumbnail is untouched) until it fits with a
 * margin — never below a floor. A small sidecar JSON records, per SO,
 * whether it fit / had to shrink / still overflows, so the Python side can
 * shorten + re-render as a last resort.
 *
 * Photoshop 2021 ExtendScript engine == ES3. No JSON.* ; we pass primitives
 * directly in the call and hand-build the result JSON.
 */

// Log + fit-result paths are overridden by the Python caller (it injects
// `YTA_PS_LOG = "..."` and `YTA_FIT_OUT = "..."` lines after this file).
var YTA_PS_LOG = "C:/Users/Usuario/Downloads/YoutubeAutomator/YoutubeAutomator/data/tmp/yta_photoshop.log";
var YTA_FIT_OUT = "";   // empty => derive from the output PNG path
function ytaPsLog(m) {
    try {
        var f = new File(YTA_PS_LOG); f.encoding = "UTF-8"; f.open("a");
        f.write((new Date()).toString() + "  " + m + "\n"); f.close();
    } catch (e) {}
}

function ytaNum(v) { return Number(v); }   // UnitValue|Number -> Number (px under PIXELS ruler)

// Tight glyph bounds of an art layer as {l,t,r,b,w,h,cx,cy} in px.
function ytaLayerDims(layer) {
    var b = layer.bounds;
    var l = ytaNum(b[0]), t = ytaNum(b[1]), r = ytaNum(b[2]), bo = ytaNum(b[3]);
    return { l: l, t: t, r: r, b: bo, w: r - l, h: bo - t, cx: (l + r) / 2, cy: (t + bo) / 2 };
}

// First TEXT art layer in a document/group, recursing into groups.
// Document.artLayers lists only TOP-LEVEL layers, so a text layer nested in a
// LayerSet would otherwise be missed and the SO misclassified as image-only.
function ytaFindTextLayer(container) {
    var i;
    for (i = 0; i < container.artLayers.length; i++) {
        if (container.artLayers[i].kind == LayerKind.TEXT) return container.artLayers[i];
    }
    for (i = 0; i < container.layerSets.length; i++) {
        var f = ytaFindTextLayer(container.layerSets[i]);
        if (f) return f;
    }
    return null;
}

/* Fit a text layer into the DESIGN BOX — the bounds the template's PLACEHOLDER
 * text occupied before we replaced it. That box is ground truth: the designer
 * sized + positioned it so it lands exactly in the visible thumbnail region
 * (the SO's own canvas can extend off-screen, and a fixed inner-canvas clips
 * the text, so neither the inner canvas nor the SO's parent bounds are the
 * right frame — only the placeholder box is). So if the new copy spills out of
 * that box we shrink the font until it fits (box minus `marginFrac` padding),
 * then re-anchor it to the box (left edge + vertical centre). Only acts when it
 * overflows; copy that already fits the box is left untouched. `origBox` is the
 * placeholder's {l,t,r,b,w,h,cx,cy} captured BEFORE the text was changed.
 * Returns {shrunk, overflow, finalScale}. */
function ytaFitTextLayer(tl, origBox, marginFrac, minScale) {
    var d = ytaLayerDims(tl);
    var tw = origBox.w * (1 - marginFrac);   // fill target = design box minus padding
    var th = origBox.h * (1 - marginFrac);

    // FILL the design box: scale the layer so the binding dimension matches the
    // box — GROW short copy to fill the space, SHRINK long copy so it never
    // clips. Scaling the LAYER (geometric, bounds-based) is reliable; setting
    // textItem.size is NOT inside an SO's doc (observed live — writing 340 read
    // back as 1130). Layer resize scales the glyph bounds linearly, so one pass
    // hits the target. Floor on shrink so tiny text stays legible.
    var shrunk = false, finalScale = 1.0;
    var s = Math.min(tw / d.w, th / d.h);
    if (s < minScale) s = minScale;
    if (Math.abs(s - 1) > 0.02) {            // only act if meaningfully off
        tl.resize(s * 100, s * 100, AnchorPosition.MIDDLECENTER);
        shrunk = (s < 1);
        finalScale = s;
        d = ytaLayerDims(tl);
    }

    // Re-anchor to the design box: left edge (where the placeholder started)
    // and vertical centre. This is what kept biting — the text could fit in
    // WIDTH yet be positioned so it ran off an edge.
    tl.translate(origBox.l - d.l, origBox.cy - d.cy);
    d = ytaLayerDims(tl);

    var overflow = (d.r > origBox.r + 1 || d.b > origBox.b + 1
                    || d.l < origBox.l - 1 || d.t < origBox.t - 1);
    return { shrunk: shrunk, overflow: overflow, finalScale: finalScale };
}

/* Nudge the text blocks toward a COMMON width WITHOUT ever shrinking — only
 * GROW a narrower block toward the widest one, and only as far as its OWN design
 * box allows (so it never clips, overlaps the other text, or crushes the art).
 * A block that's already the widest, or has no room to grow (it fills its box
 * height), is left at full size. This keeps every text as BIG as its box
 * permits — earlier we shrank the wider block to match the narrower, which made
 * the whole thing small. Anchored MIDDLELEFT (keeps left edge + vertical centre). */
function ytaEqualizeWidths(sos, marginFrac) {
    var info = [], i, maxW = -1;
    for (i = 0; i < sos.length; i++) {
        var d = ytaLayerDims(sos[i].layer);
        var bw = sos[i].box.w * (1 - marginFrac), bh = sos[i].box.h * (1 - marginFrac);
        var headroom = Math.min(bw / d.w, bh / d.h);   // growth before hitting its own box
        info.push({ layer: sos[i].layer, role: sos[i].role, w: d.w, maxReach: d.w * headroom });
        if (d.w > maxW) maxW = d.w;
    }
    for (i = 0; i < info.length; i++) {
        var target = Math.min(maxW, info[i].maxReach);   // grow toward widest, capped by own box
        var f = target / info[i].w;
        if (f > 1.02) {                                  // GROW only — never shrink
            info[i].layer.resize(f * 100, f * 100, AnchorPosition.MIDDLELEFT);
        }
        ytaPsLog("[equalize] " + info[i].role + " w=" + info[i].w.toFixed(0)
                 + " -> " + target.toFixed(0) + " (x" + f.toFixed(3) + ")");
    }
}

function ytaJsonStr(s) {
    s = String(s);
    var out = "", i, c;
    for (i = 0; i < s.length; i++) {
        c = s.charAt(i);
        if (c == '"' || c == '\\') out += "\\" + c;
        else if (c == "\n") out += "\\n";
        else if (c == "\r") out += "\\r";
        else if (c == "\t") out += "\\t";
        else out += c;
    }
    return '"' + out + '"';
}

function ytaWriteFit(path, results) {
    try {
        var parts = [];
        for (var i = 0; i < results.length; i++) {
            var r = results[i];
            parts.push(
                '{"role":' + ytaJsonStr(r.role) +
                ',"text":' + ytaJsonStr(r.text) +
                ',"shrunk":' + (r.shrunk ? "true" : "false") +
                ',"overflow":' + (r.overflow ? "true" : "false") +
                ',"final_scale":' + (Math.round(r.finalScale * 1000) / 1000) + '}'
            );
        }
        var f = new File(path); f.encoding = "UTF-8"; f.open("w");
        f.write("[" + parts.join(",") + "]"); f.close();
    } catch (e) { ytaPsLog("fit-write failed: " + e); }
}

function ytaRenderThumb(templatePath, topText, bottomText, outputPath,
                        autofit, marginFrac, minScale) {
    if (autofit === undefined) autofit = true;
    if (marginFrac === undefined) marginFrac = 0.06;
    if (minScale === undefined) minScale = 0.35;
    ytaPsLog("ytaRenderThumb: " + templatePath + " | " + topText + " / " + bottomText
             + " | autofit=" + autofit);

    var origUnits = app.preferences.rulerUnits;
    app.preferences.rulerUnits = Units.PIXELS;

    var tf = new File(templatePath);
    ytaPsLog("template exists: " + tf.exists);
    if (!tf.exists) {
        throw new Error("template not found: " + templatePath);
    }
    var doc = app.open(tf);
    ytaPsLog("opened: " + doc.name + "  layers=" + doc.layers.length);

    var fitResults = [];
    var placedSOs = [];

    // Edit text inside a Smart Object. Returns true if a text layer was found
    // and replaced (also auto-fitting it); false (and reverts the SO) if the
    // SO holds image content -- so we know to skip and try the next one.
    function tryReplaceTextInSO(soLayer, newText, role) {
        // The SO's footprint on the PARENT (with its placeholder) ≈ the design
        // region on the thumbnail; kept for the width-equalise pass below.
        var parentBox = ytaLayerDims(soLayer);
        doc.activeLayer = soLayer;
        executeAction(stringIDToTypeID("placedLayerEditContents"),
                      new ActionDescriptor(), DialogModes.NO);
        var inner = app.activeDocument;
        var found = ytaFindTextLayer(inner);   // recurses into groups
        if (!found) {
            // image SO -- close without saving, the outer doc is unchanged
            inner.close(SaveOptions.DONOTSAVECHANGES);
            return false;
        }
        // Capture the PLACEHOLDER's box BEFORE replacing the text — that box is
        // the design-correct region (visible, un-clipped) we must fit into.
        var origBox = ytaLayerDims(found);
        found.textItem.contents = newText;

        var fit = { role: role, text: newText, shrunk: false, overflow: false, finalScale: 1.0 };
        if (autofit) {
            try {
                var b0 = ytaLayerDims(found);
                var r = ytaFitTextLayer(found, origBox, marginFrac, minScale);
                var b1 = ytaLayerDims(found);
                fit.shrunk = r.shrunk; fit.overflow = r.overflow; fit.finalScale = r.finalScale;
                ytaPsLog("[fit] " + role
                         + " box=[" + origBox.l.toFixed(0) + "," + origBox.r.toFixed(0) + "]"
                         + " newtext=[" + b0.l.toFixed(0) + "," + b0.r.toFixed(0) + "]"
                         + " fitted=[" + b1.l.toFixed(0) + "," + b1.r.toFixed(0) + "]"
                         + " shrunk=" + r.shrunk + " overflow=" + r.overflow
                         + " scale=" + r.finalScale.toFixed(3));
            } catch (e) { ytaPsLog("[fit] " + role + " error: " + e); }
        }
        fitResults.push(fit);
        inner.close(SaveOptions.SAVECHANGES);   // back to the parent; the SO updates
        placedSOs.push({ layer: soLayer, box: parentBox, role: role });
        return true;
    }

    // Walk Smart Objects top-to-bottom; replace into the first 2 that
    // actually contain a text layer (skipping image-content SOs).
    var replaced = 0;
    var texts = [topText, bottomText];
    var roles = ["top", "bottom"];
    for (var i = 0; i < doc.layers.length && replaced < 2; i++) {
        var L = doc.layers[i];
        if (L.kind != LayerKind.SMARTOBJECT) continue;
        var ok = tryReplaceTextInSO(L, texts[replaced], roles[replaced]);
        ytaPsLog((ok ? "[text] " : "[skip-image] ") + L.name);
        if (ok) replaced++;
    }
    if (replaced < 2) {
        doc.close(SaveOptions.DONOTSAVECHANGES);
        throw new Error("template needs >=2 TEXT smart objects, found " + replaced
            + " (each text Smart Object must be top-level on the canvas and hold a text "
            + "layer; point text is recommended over paragraph/box text for auto-fit)");
    }

    // Equalise the two text blocks to a common WIDTH so they stack as a tidy
    // left-aligned column (both edges line up) — better-balanced sizing. Each
    // text already fills its own design box (different box widths => different
    // text widths), so we scale every text SO to the widest common width that
    // still fits ALL their boxes (grows the narrow one if it has room, shrinks
    // the wide one otherwise — never past a box, so no clipping/crushing).
    if (autofit && placedSOs.length >= 2) {
        try {
            ytaEqualizeWidths(placedSOs, marginFrac);
        } catch (e) { ytaPsLog("[equalize] error: " + e); }
    }

    // Write the fit sidecar (Python reads it to decide on a shorten+retry).
    var fitPath = YTA_FIT_OUT;
    if (!fitPath) fitPath = String(outputPath).replace(/\.[^.\/\\]+$/, "") + ".thumbfit.json";
    ytaWriteFit(fitPath, fitResults);

    // Export PNG at the template's existing size.
    var pngOpts = new PNGSaveOptions();
    pngOpts.interlaced = false;
    pngOpts.compression = 6;
    doc.saveAs(new File(outputPath), pngOpts, true, Extension.LOWERCASE);
    ytaPsLog("saved PNG: " + outputPath);

    doc.close(SaveOptions.DONOTSAVECHANGES);
    app.preferences.rulerUnits = origUnits;
    return outputPath;
}
