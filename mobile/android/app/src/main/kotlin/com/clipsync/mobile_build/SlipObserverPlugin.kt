package com.clipsync.mobile_build

import android.content.ContentResolver
import android.content.ContentUris
import android.content.Context
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.io.IOException

class SlipObserverPlugin : FlutterPlugin, EventChannel.StreamHandler,
    MethodChannel.MethodCallHandler {
    private var observer: ContentObserver? = null
    private var resolver: ContentResolver? = null
    private var appContext: Context? = null
    private var methodChannel: MethodChannel? = null
    private var lastId: Long = -1

    // folder ของแอปธนาคาร — ปรับตามเครื่องจริงตอน Gate 1
    private val bankBuckets = setOf("SCB Easy", "KPLUS", "Bualuang mBanking", "Screenshots")

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        appContext = binding.applicationContext
        resolver = binding.applicationContext.contentResolver
        EventChannel(binding.binaryMessenger, "clipsync/slip_events").setStreamHandler(this)
        methodChannel = MethodChannel(binding.binaryMessenger, "clipsync/slip_methods").also {
            it.setMethodCallHandler(this)
        }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "copyContentUriToCache" -> {
                val uriString = call.arguments as? String
                if (uriString.isNullOrBlank()) {
                    result.error("bad_args", "uriString required", null)
                    return
                }
                try {
                    result.success(copyContentUriToCache(uriString))
                } catch (e: Exception) {
                    result.error("copy_failed", e.message, null)
                }
            }
            else -> result.notImplemented()
        }
    }

    private fun copyContentUriToCache(uriString: String): String {
        val context = appContext ?: throw IllegalStateException("plugin not attached")
        val uri = Uri.parse(uriString)
        val outFile = File(context.cacheDir, "slip_${System.currentTimeMillis()}.jpg")
        context.contentResolver.openInputStream(uri)?.use { input ->
            outFile.outputStream().use { output -> input.copyTo(output) }
        } ?: throw IOException("Cannot open $uriString")
        return outFile.absolutePath
    }

    override fun onListen(args: Any?, events: EventChannel.EventSink?) {
        observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean, uri: Uri?) {
                queryLatest(events)
            }
        }
        resolver?.registerContentObserver(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true, observer!!)
        queryLatest(events)
    }

    private fun matchesBankFolder(bucket: String, relativePath: String): Boolean {
        return bankBuckets.any { marker ->
            bucket.contains(marker, ignoreCase = true) ||
                relativePath.contains(marker, ignoreCase = true)
        }
    }

    private fun queryLatest(events: EventChannel.EventSink?) {
        val proj = arrayOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DATA,
            MediaStore.Images.Media.BUCKET_DISPLAY_NAME,
            MediaStore.Images.Media.DATE_ADDED,
            MediaStore.Images.Media.RELATIVE_PATH)
        resolver?.query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, proj,
            null, null, "${MediaStore.Images.Media.DATE_ADDED} DESC")?.use { c ->
            if (c.moveToFirst()) {
                val id = c.getLong(0)
                if (id == lastId) return
                lastId = id
                val bucket = c.getString(2) ?: ""
                val relativePath = c.getString(4) ?: ""
                if (!matchesBankFolder(bucket, relativePath)) return
                val uri = ContentUris.withAppendedId(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id).toString()
                val dataPath = c.getString(1)
                val path = dataPath?.takeIf { it.isNotEmpty() } ?: uri
                events?.success(mapOf(
                    "uri" to uri,
                    "path" to path,
                    "bucket" to bucket,
                    "relative_path" to relativePath,
                    "date_added" to c.getLong(3)))
            }
        }
    }

    override fun onCancel(args: Any?) {
        unregisterObserver()
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        unregisterObserver()
        methodChannel?.setMethodCallHandler(null)
        methodChannel = null
        resolver = null
        appContext = null
    }

    private fun unregisterObserver() {
        observer?.let { resolver?.unregisterContentObserver(it) }
        observer = null
    }
}
