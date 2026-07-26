import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Max individual withdraw notifies in the Android shade (option B).
const kMaxShadeWithdrawNotifies = 20;

/// Pure helper — newest-first [pending] capped for shade children.
List<T> takeShadeWithdrawOrders<T>(List<T> pending, {int max = kMaxShadeWithdrawNotifies}) {
  if (max <= 0) return const [];
  if (pending.length <= max) return List<T>.from(pending);
  return pending.take(max).toList(growable: false);
}

/// Dart API for native Android withdraw shade notifications.
///
/// Copy actions are handled in Kotlin via ClipboardCopyReceiver.
class WithdrawNativeNotify {
  WithdrawNativeNotify._();

  static const MethodChannel channel = MethodChannel(
    'com.clipsync.mobile_build/withdraw_notify',
  );

  static bool _listening = false;

  /// Listen for native body-tap events while the Flutter UI is already running.
  static void ensureOpenInboxListener(void Function(String orderId) onOpen) {
    if (_listening) return;
    _listening = true;
    channel.setMethodCallHandler((call) async {
      if (call.method == 'onOpenWithdrawInbox') {
        final id = '${call.arguments ?? ''}'.trim();
        if (id.isNotEmpty) onOpen(id);
      }
      return null;
    });
  }

  /// Replace shade children with [orders] (max [kMaxShadeWithdrawNotifies]).
  ///
  /// Each map: orderId, amount, account, bank, accountName, body, title, canCopy.
  static Future<void> syncVisible({
    required List<Map<String, Object?>> orders,
    String headsUpOrderId = '',
    int pendingCount = 0,
  }) async {
    if (kIsWeb) return;
    final capped = takeShadeWithdrawOrders(orders);
    try {
      await channel.invokeMethod<void>('syncVisible', <String, Object?>{
        'orders': capped,
        'headsUpOrderId': headsUpOrderId,
        'pendingCount': pendingCount > 0 ? pendingCount : capped.length,
      });
    } on MissingPluginException {
      // Desktop/tests without the Android plugin.
    } on PlatformException catch (e) {
      // FGS isolate often lacks the plugin — never rethrow (diagnostics spam).
      debugPrint(
        'WithdrawNativeNotify.syncVisible PlatformException: ${e.code} ${e.message}',
      );
    }
  }

  /// Post a single detail notify (legacy). Prefer [syncVisible].
  static Future<void> show({
    required String orderId,
    required String amount,
    required String account,
    String bank = '',
    String accountName = '',
    required String body,
    String title = 'รายการถอนใหม่',
    bool canCopy = true,
    bool headsUp = true,
    int pendingCount = 1,
  }) async {
    await syncVisible(
      orders: [
        <String, Object?>{
          'orderId': orderId,
          'amount': amount,
          'account': account,
          'bank': bank,
          'accountName': accountName,
          'body': body,
          'title': title,
          'canCopy': canCopy,
        },
      ],
      headsUpOrderId: headsUp ? orderId : '',
      pendingCount: pendingCount,
    );
  }

  static Future<void> cancel({int id = 41001}) async {
    if (kIsWeb) return;
    try {
      await channel.invokeMethod<void>('cancel', <String, Object?>{'id': id});
    } on MissingPluginException {
      // ignore
    } on PlatformException catch (e) {
      debugPrint('WithdrawNativeNotify.cancel PlatformException: ${e.code}');
    }
  }

  static Future<void> cancelAll() async {
    if (kIsWeb) return;
    try {
      await channel.invokeMethod<void>('cancelAll');
    } on MissingPluginException {
      // ignore
    } on PlatformException catch (e) {
      debugPrint('WithdrawNativeNotify.cancelAll PlatformException: ${e.code}');
    }
  }

  /// Consume a pending body-tap order id from MainActivity intent extras.
  static Future<String?> takeOpenInboxOrderId() async {
    if (kIsWeb) return null;
    try {
      final raw = await channel.invokeMethod<String?>('takeOpenInboxOrderId');
      final id = raw?.trim() ?? '';
      return id.isEmpty ? null : id;
    } on MissingPluginException {
      return null;
    } on PlatformException {
      return null;
    }
  }
}
