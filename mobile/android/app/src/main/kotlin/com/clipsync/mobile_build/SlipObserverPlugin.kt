package com.clipsync.mobile_build

import android.content.ContentResolver
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.EventChannel

class SlipObserverPlugin : FlutterPlugin, EventChannel.StreamHandler {
    private var observer: ContentObserver? = null
    private var resolver: ContentResolver? = null
    private var lastId: Long = -1

    // folder ของแอปธนาคาร — ปรับตามเครื่องจริงตอน Gate 1
    private val bankBuckets = setOf("SCB Easy", "KPLUS", "Bualuang mBanking", "Screenshots")

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        resolver = binding.applicationContext.contentResolver
        EventChannel(binding.binaryMessenger, "clipsync/slip_events").setStreamHandler(this)
    }

    override fun onListen(args: Any?, events: EventChannel.EventSink?) {
        observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean, uri: Uri?) {
                queryLatest(events)
            }
        }
        resolver?.registerContentObserver(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true, observer!!)
    }

    private fun queryLatest(events: EventChannel.EventSink?) {
        val proj = arrayOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DATA,
            MediaStore.Images.Media.BUCKET_DISPLAY_NAME,
            MediaStore.Images.Media.DATE_ADDED)
        resolver?.query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, proj,
            null, null, "${MediaStore.Images.Media.DATE_ADDED} DESC")?.use { c ->
            if (c.moveToFirst()) {
                val id = c.getLong(0)
                if (id == lastId) return
                lastId = id
                val bucket = c.getString(2) ?: ""
                if (bankBuckets.any { bucket.contains(it, ignoreCase = true) }) {
                    events?.success(mapOf(
                        "path" to c.getString(1),
                        "bucket" to bucket,
                        "date_added" to c.getLong(3)))
                }
            }
        }
    }

    override fun onCancel(args: Any?) {
        unregisterObserver()
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        unregisterObserver()
        resolver = null
    }

    private fun unregisterObserver() {
        observer?.let { resolver?.unregisterContentObserver(it) }
        observer = null
    }
}
