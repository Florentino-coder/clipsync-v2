package com.clipsync.mobile_build.withdraw

import android.app.Activity
import android.content.Intent
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.embedding.engine.plugins.activity.ActivityAware
import io.flutter.embedding.engine.plugins.activity.ActivityPluginBinding
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugin.common.PluginRegistry

/**
 * MethodChannel bridge for [WithdrawalNotifier].
 * Channel: `com.clipsync.mobile_build/withdraw_notify`
 */
class WithdrawNotifyPlugin : FlutterPlugin, MethodChannel.MethodCallHandler,
    ActivityAware, PluginRegistry.NewIntentListener {

    private var channel: MethodChannel? = null
    private var appContext: android.content.Context? = null
    private var activity: Activity? = null
    private var pendingOpenOrderId: String? = null

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        appContext = binding.applicationContext
        channel = MethodChannel(binding.binaryMessenger, CHANNEL).also {
            it.setMethodCallHandler(this)
        }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        val context = appContext
        if (context == null) {
            result.error("no_context", "plugin not attached", null)
            return
        }
        when (call.method) {
            "show" -> {
                val args = call.arguments as? Map<*, *>
                if (args == null) {
                    result.error("bad_args", "map required", null)
                    return
                }
                val data = parseNotifyData(args)
                WithdrawalNotifier.notify(context, data)
                result.success(null)
            }
            "syncVisible" -> {
                val args = call.arguments as? Map<*, *>
                if (args == null) {
                    result.error("bad_args", "map required", null)
                    return
                }
                try {
                    val rawOrders = args["orders"]
                    val list = mutableListOf<WithdrawalNotifier.NotifyData>()
                    if (rawOrders is List<*>) {
                        for (item in rawOrders) {
                            val m = item as? Map<*, *> ?: continue
                            list.add(parseNotifyData(m))
                        }
                    }
                    val capped = list.take(WithdrawalNotifier.MAX_VISIBLE)
                    val headsUpOrderId = args.string("headsUpOrderId").ifBlank { null }
                    val pendingCount = args.int("pendingCount", capped.size)
                    WithdrawalNotifier.syncVisible(
                        context,
                        capped,
                        headsUpOrderId,
                        pendingCount,
                    )
                    result.success(null)
                } catch (e: Exception) {
                    result.error("sync_failed", e.message, null)
                }
            }
            "cancel" -> {
                val args = call.arguments as? Map<*, *>
                val id = args?.int("id", WithdrawalNotifier.DETAIL_NOTIFY_ID)
                    ?: WithdrawalNotifier.DETAIL_NOTIFY_ID
                WithdrawalNotifier.cancel(context, id)
                result.success(null)
            }
            "cancelAll" -> {
                WithdrawalNotifier.cancelAll(context)
                result.success(null)
            }
            "takeOpenInboxOrderId" -> {
                if (pendingOpenOrderId.isNullOrBlank()) {
                    captureOpenInboxFromIntent(activity?.intent, notifyDart = false)
                }
                val id = pendingOpenOrderId
                pendingOpenOrderId = null
                result.success(id)
            }
            else -> result.notImplemented()
        }
    }

    private fun parseNotifyData(args: Map<*, *>): WithdrawalNotifier.NotifyData =
        WithdrawalNotifier.NotifyData(
            orderId = args.string("orderId"),
            amount = args.string("amount"),
            account = args.string("account"),
            bank = args.string("bank"),
            accountName = args.string("accountName"),
            body = args.string("body"),
            title = args.string("title").ifBlank { "รายการถอนใหม่" },
            canCopy = args.bool("canCopy", true),
            headsUp = args.bool("headsUp", true),
            pendingCount = args.int("pendingCount", 1),
        )

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel?.setMethodCallHandler(null)
        channel = null
        appContext = null
    }

    override fun onAttachedToActivity(binding: ActivityPluginBinding) {
        activity = binding.activity
        binding.addOnNewIntentListener(this)
        captureOpenInboxFromIntent(binding.activity.intent, notifyDart = false)
    }

    override fun onDetachedFromActivityForConfigChanges() {
        activity = null
    }

    override fun onReattachedToActivityForConfigChanges(binding: ActivityPluginBinding) {
        activity = binding.activity
        binding.addOnNewIntentListener(this)
        captureOpenInboxFromIntent(binding.activity.intent, notifyDart = false)
    }

    override fun onDetachedFromActivity() {
        activity = null
    }

    override fun onNewIntent(intent: Intent): Boolean {
        captureOpenInboxFromIntent(intent, notifyDart = true)
        activity?.intent = intent
        return false
    }

    private fun captureOpenInboxFromIntent(intent: Intent?, notifyDart: Boolean) {
        if (intent == null) return
        val open = intent.getBooleanExtra(WithdrawalNotifier.EXTRA_OPEN_WITHDRAW_INBOX, false)
        if (!open) return
        val orderId = intent.getStringExtra(WithdrawalNotifier.EXTRA_ORDER_ID)?.trim().orEmpty()
        if (orderId.isNotEmpty()) {
            if (notifyDart) {
                // Warm start: Flutter is alive — push open-inbox. Cold start uses takeOpenInboxOrderId.
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    channel?.invokeMethod("onOpenWithdrawInbox", orderId)
                }
            } else {
                pendingOpenOrderId = orderId
            }
        }
        // Clear so re-reads don't re-open forever.
        intent.removeExtra(WithdrawalNotifier.EXTRA_OPEN_WITHDRAW_INBOX)
        intent.removeExtra(WithdrawalNotifier.EXTRA_ORDER_ID)
    }

    private fun Map<*, *>.string(key: String): String =
        this[key]?.toString()?.trim().orEmpty()

    private fun Map<*, *>.bool(key: String, default: Boolean): Boolean {
        val v = this[key] ?: return default
        return when (v) {
            is Boolean -> v
            is Number -> v.toInt() != 0
            else -> v.toString().equals("true", ignoreCase = true)
        }
    }

    private fun Map<*, *>.int(key: String, default: Int): Int {
        val v = this[key] ?: return default
        return when (v) {
            is Number -> v.toInt()
            else -> v.toString().toIntOrNull() ?: default
        }
    }

    companion object {
        const val CHANNEL = "com.clipsync.mobile_build/withdraw_notify"
    }
}
