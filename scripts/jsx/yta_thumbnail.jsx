/*
 * yta_thumbnail.jsx — function injected into Photoshop via COM
 * (Application.DoJavaScript). Replaces the 2 text Smart Objects in a
 * thumbnail template with new top + bottom text and exports a PNG.
 *
 * Photoshop 2021 ExtendScript engine == ES3. No JSON.parse on older
 * builds; we pass primitives directly in the function call.
 */

// Log path is fixed -- Python tails it on errors so we always see where
// the JSX got to even when COM swallows the exception.
var YTA_PS_LOG = "C:/Users/Usuario/Downloads/YoutubeAutomator/YoutubeAutomator/data/tmp/yta_photoshop.log";
function ytaPsLog(m) {
    try {
        var f = new File(YTA_PS_LOG); f.encoding = "UTF-8"; f.open("a");
        f.write((new Date()).toString() + "  " + m + "\n"); f.close();
    } catch (e) {}
}

function ytaRenderThumb(templatePath, topText, bottomText, outputPath) {
    ytaPsLog("ytaRenderThumb called: " + templatePath + " | " + topText + " / " + bottomText);

    var origUnits = app.preferences.rulerUnits;
    app.preferences.rulerUnits = Units.PIXELS;

    var tf = new File(templatePath);
    ytaPsLog("template exists: " + tf.exists);
    if (!tf.exists) {
        throw new Error("template not found: " + templatePath);
    }
    var doc = app.open(tf);
    ytaPsLog("opened: " + doc.name + "  layers=" + doc.layers.length);

    // Try to edit text inside a Smart Object. Returns true if a text layer
    // was found and replaced; false (and reverts the SO) if the SO holds
    // image content instead -- so we know to skip and try the next one.
    function tryReplaceTextInSO(soLayer, newText) {
        doc.activeLayer = soLayer;
        executeAction(stringIDToTypeID("placedLayerEditContents"),
                      new ActionDescriptor(), DialogModes.NO);
        var inner = app.activeDocument;
        var found = null;
        for (var j = 0; j < inner.artLayers.length; j++) {
            var AL = inner.artLayers[j];
            if (AL.kind == LayerKind.TEXT) { found = AL; break; }
        }
        if (!found) {
            // image SO -- close without saving, the outer doc is unchanged
            inner.close(SaveOptions.DONOTSAVECHANGES);
            return false;
        }
        found.textItem.contents = newText;
        inner.close(SaveOptions.SAVECHANGES);
        return true;
    }

    // Walk Smart Objects top-to-bottom; replace into the first 2 that
    // actually contain a text layer (skipping image-content SOs).
    var replaced = 0;
    var texts = [topText, bottomText];
    for (var i = 0; i < doc.layers.length && replaced < 2; i++) {
        var L = doc.layers[i];
        if (L.kind != LayerKind.SMARTOBJECT) continue;
        var ok = tryReplaceTextInSO(L, texts[replaced]);
        ytaPsLog((ok ? "[text] " : "[skip-image] ") + L.name);
        if (ok) replaced++;
    }
    if (replaced < 2) {
        doc.close(SaveOptions.DONOTSAVECHANGES);
        throw new Error("template needs >=2 TEXT smart objects, found " + replaced);
    }

    // Export PNG at the template's existing size (set sequence-wide thumb
    // size in metadata or by template; keep PNG identical to the canvas).
    var pngOpts = new PNGSaveOptions();
    pngOpts.interlaced = false;
    pngOpts.compression = 6;
    doc.saveAs(new File(outputPath), pngOpts, true, Extension.LOWERCASE);
    ytaPsLog("saved PNG: " + outputPath);

    doc.close(SaveOptions.DONOTSAVECHANGES);
    app.preferences.rulerUnits = origUnits;
    return outputPath;
}
