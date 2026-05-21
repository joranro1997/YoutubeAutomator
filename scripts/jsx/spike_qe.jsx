/*
 * spike_qe.jsx — SAFE QE DOM introspection for Premiere Pro 14.3.1.
 *
 * QE DOM crashes Premiere if a mutating call gets a wrong arg/time format.
 * So this probe is STRICTLY READ-ONLY: it only enumerates method names and
 * reads simple properties. It performs NO mutation (no createNewSequence,
 * no insertClip, no setStart/razor/move). It cannot crash Premiere.
 *
 * Run with lom_nest.prproj open, via the ExtendScript Debugger.
 * Output: data/tmp/spike_qe.json   (DO NOT SAVE the project afterwards.)
 */
(function () {
    var REPO = "C:/Users/Usuario/Downloads/YoutubeAutomator/YoutubeAutomator";

    function q(s){s=String(s);var o='"',i,c,n;for(i=0;i<s.length;i++){c=s.charAt(i);n=s.charCodeAt(i);
      if(c=='"')o+='\\"';else if(c=='\\')o+='\\\\';else if(c=='\n')o+='\\n';else if(c=='\r')o+='\\r';
      else if(c=='\t')o+='\\t';else if(n<32)o+='\\u'+('0000'+n.toString(16)).slice(-4);else o+=c;}return o+'"';}
    function S(v,d){d=d||0;var p="",i;for(i=0;i<d;i++)p+="  ";var pi=p+"  ",t=typeof v;
      if(v===null||v===undefined)return"null";if(t=="boolean")return v?"true":"false";
      if(t=="number")return isFinite(v)?String(v):"null";if(t=="string")return q(v);
      if(v instanceof Array){if(!v.length)return"[]";var a=[];for(i=0;i<v.length;i++)a.push(pi+S(v[i],d+1));
        return"[\n"+a.join(",\n")+"\n"+p+"]";}
      var k,ks=[];for(k in v)if(v.hasOwnProperty(k))ks.push(k);if(!ks.length)return"{}";
      var b=[];for(i=0;i<ks.length;i++)b.push(pi+q(ks[i])+": "+S(v[ks[i]],d+1));
      return"{\n"+b.join(",\n")+"\n"+p+"}";}
    function safe(fn,fb){try{var r=fn();return r===undefined?fb:r;}catch(e){return{__error:String(e)};}}
    /* Enumerate names + whether each is a function. NO calls are made. */
    function surface(obj){var out=[],k;if(!obj)return out;
      for(k in obj){var isFn=false;try{isFn=(typeof obj[k]=="function");}catch(e){}
        out.push(isFn?(k+"()"):k);}out.sort();return out;}

    var R={ project:{}, qe:{}, surfaces:{}, reads:{} };
    R.project.version=safe(function(){return app.version;});
    R.project.name=safe(function(){return app.project.name;});
    R.project.activeSequence=safe(function(){return app.project.activeSequence?app.project.activeSequence.name:null;});

    R.qe.enabled=safe(function(){app.enableQE();return (typeof qe!=="undefined")&&!!qe;},false);
    if(R.qe.enabled!==true){
      var f0=new File(REPO+"/data/tmp/spike_qe.json");f0.encoding="UTF-8";f0.open("w");f0.write(S(R));f0.close();
      alert("QE not available — see spike_qe.json"); return;
    }

    // Reflection only — these are read accessors, no timeline mutation.
    var qproj=safe(function(){return qe.project;});
    R.surfaces.qe_project=surface(qproj);

    var qseq=safe(function(){return qe.project.getActiveSequence();});
    R.qe.activeSeqName=safe(function(){return qseq?qseq.name:null;});
    R.qe.numVideoTracks=safe(function(){return qseq?qseq.numVideoTracks:null;});
    R.qe.numAudioTracks=safe(function(){return qseq?qseq.numAudioTracks:null;});
    R.surfaces.qe_sequence=surface(qseq);

    var qvt=safe(function(){return qseq?qseq.getVideoTrackAt(6):null;}); // V7
    R.qe.v7_numItems=safe(function(){return qvt?qvt.numItems:null;});
    R.surfaces.qe_videoTrack=surface(qvt);

    var qclip=safe(function(){return (qvt&&qvt.numItems>0)?qvt.getItemAt(0):null;});
    R.surfaces.qe_trackItem=surface(qclip);
    R.reads.clip0={
      name:safe(function(){return qclip?qclip.name:null;}),
      start:safe(function(){return qclip?String(qclip.start):null;}),
      end:safe(function(){return qclip?String(qclip.end):null;}),
      inPoint:safe(function(){return qclip?String(qclip.inPoint):null;}),
      outPoint:safe(function(){return qclip?String(qclip.outPoint):null;}),
      duration:safe(function(){return qclip?String(qclip.duration):null;})
    };

    var qat=safe(function(){return qseq?qseq.getAudioTrackAt(0):null;});
    R.surfaces.qe_audioTrack=surface(qat);

    // Also reflect public-API objects for comparison (no mutation).
    R.surfaces.pub_trackItem=safe(function(){
      var s=app.project.activeSequence; return s?surface(s.videoTracks[6].clips[0]):null;});
    R.surfaces.pub_projectItem=safe(function(){
      var s=app.project.activeSequence; return s?surface(s.videoTracks[6].clips[0].projectItem):null;});

    var fld=new Folder(REPO+"/data/tmp"); if(!fld.exists)fld.create();
    var f=new File(REPO+"/data/tmp/spike_qe.json"); f.encoding="UTF-8"; f.open("w"); f.write(S(R)); f.close();
    alert("SAFE QE introspection done (no mutation).\n\n"
      +"QE: "+R.qe.enabled+"\nV7 items: "+S(R.qe.v7_numItems)
      +"\n\nWritten: data/tmp/spike_qe.json\nDo NOT save the project.");
})();
