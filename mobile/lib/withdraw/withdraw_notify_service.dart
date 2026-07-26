import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import 'withdraw_native_notify.dart';
import 'withdraw_queue.dart';

const kWithdrawDetailNotifyId = 41001;
const kWithdrawSummaryNotifyId = 41000;
const kWithdrawChannelId = 'withdraw_alerts';
const kWithdrawChannelName = 'Withdraw alerts';
const kCopyAmountActionId = 'copy_amount';
const kCopyAccountActionId = 'copy_account';

/// Pure throttle helper — full heads-up if queue was empty or last heads-up ≥4s ago.
bool shouldHeadsUp({
  required bool wasEmpty,
  required DateTime? lastHeadsUp,
  required DateTime now,
}) {
  if (wasEmpty) return true;
  if (lastHeadsUp == null) return true;
  return now.difference(lastHeadsUp) >= const Duration(seconds: 4);
}

String encodeWithdrawNotifyPayload({
  required String orderId,
  required String amount,
  required String account,
}) {
  return jsonEncode({
    'order_id': orderId,
    'amount': amount,
    'account': account,
  });
}

Map<String, String>? decodeWithdrawNotifyPayload(String? raw) {
  if (raw == null || raw.trim().isEmpty) return null;
  try {
    final decoded = jsonDecode(raw);
    if (decoded is! Map) return null;
    String read(String k) => '${decoded[k] ?? ''}'.trim();
    final out = {
      'order_id': read('order_id'),
      'amount': read('amount'),
      'account': read('account'),
    };
    if (out['amount']!.isEmpty && out['account']!.isEmpty) return null;
    return out;
  } catch (_) {
    final id = raw.trim();
    if (id.isEmpty) return null;
    return {'order_id': id, 'amount': '', 'account': ''};
  }
}

String? copyTextForAction(String? actionId, Map<String, String> data) {
  if (actionId == kCopyAmountActionId) {
    final t = data['amount']?.trim() ?? '';
    return t.isEmpty ? null : t;
  }
  if (actionId == kCopyAccountActionId) {
    final t = data['account']?.trim() ?? '';
    return t.isEmpty ? null : t;
  }
  return null;
}

/// Payload-first copy text for notification actions; queue values are fallback only.
String? resolveWithdrawCopyText({
  required String? actionId,
  required String? payload,
  String? queueAmount,
  String? queueAccount,
}) {
  if (actionId != kCopyAmountActionId && actionId != kCopyAccountActionId) {
    return null;
  }
  final data = decodeWithdrawNotifyPayload(payload);
  if (data != null) {
    final fromPayload = copyTextForAction(actionId, data);
    if (fromPayload != null && fromPayload.isNotEmpty) return fromPayload;
  }
  if (actionId == kCopyAmountActionId) {
    final t = queueAmount?.trim() ?? '';
    return t.isEmpty ? null : t;
  }
  final t = queueAccount?.trim() ?? '';
  return t.isEmpty ? null : t;
}

String formatWithdrawNotifyBody({
  required String amount,
  required String account,
  required String bank,
  required String accountName,
  String? withdrawAt,
  String? approvedAt,
}) {
  return formatWithdrawInboxLines(
    amount: amount,
    account: account,
    bank: bank,
    accountName: accountName,
    withdrawAt: withdrawAt,
    approvedAt: approvedAt,
  ).join('\n');
}

/// Shared notify + inbox display lines (emoji + Thai labels).
/// Product order: bank → account name → account number → amount → approved_at.
List<String> formatWithdrawInboxLines({
  required String amount,
  required String account,
  required String bank,
  required String accountName,
  String? stateLabel,
  String? withdrawAt,
  String? approvedAt,
}) {
  final lines = <String>[];
  final bankLabel = bank.trim();
  if (bankLabel.isNotEmpty) {
    lines.add('🏧 ธนาคาร: $bankLabel');
  }
  final name = accountName.trim();
  if (name.isNotEmpty) {
    lines.add('👤 ชื่อบัญชี: $name');
  }
  final acct = account.trim();
  if (acct.isNotEmpty) {
    lines.add('🏦 เลขบัญชี: $acct');
  }
  final amt = amount.trim();
  if (amt.isNotEmpty) {
    lines.add('💰 จำนวนเงิน: $amt');
  }
  final approved = approvedAt?.trim() ?? '';
  if (approved.isNotEmpty) {
    lines.add('✅ อนุมัติ: $approved');
  }
  // withdrawAt intentionally omitted from notify/inbox body (product: 5 fields).
  final state = stateLabel?.trim() ?? '';
  if (state.isNotEmpty) {
    lines.add(state);
  }
  return lines;
}

typedef CopyHandler = Future<void> Function(String actionId, String text);

/// Called when user taps the withdraw notification body (not a copy action).
/// [orderId] comes from notification payload when available.
typedef OpenInboxHandler = void Function(String? orderId);

/// Injectable queue accessor for notification copy actions.
WithdrawQueue Function()? withdrawQueueProvider;

/// Open-inbox handler registered by the main UI (HomeScreen).
OpenInboxHandler? onOpenWithdrawInbox;

/// Local notifications for pending withdraw orders (HIGH channel, separate from FGS).
class WithdrawNotifyService {
  WithdrawNotifyService({
    FlutterLocalNotificationsPlugin? plugin,
    @Deprecated('Phase A: native ClipboardCopyReceiver owns shade copy')
    CopyHandler? onCopy,
    DateTime Function()? clock,
  })  : _plugin = plugin ?? FlutterLocalNotificationsPlugin(),
        _clock = clock ?? DateTime.now;

  static final WithdrawNotifyService instance = WithdrawNotifyService();

  final FlutterLocalNotificationsPlugin _plugin;
  final DateTime Function() _clock;

  DateTime? _lastHeadsUp;
  bool _initialized = false;

  DateTime? get lastHeadsUp => _lastHeadsUp;

  @visibleForTesting
  void setLastHeadsUpForTest(DateTime? value) => _lastHeadsUp = value;

  Future<void> init() async {
    if (_initialized) return;

    const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
    const initSettings = InitializationSettings(android: androidInit);

    await _plugin.initialize(
      initSettings,
      onDidReceiveNotificationResponse: _onNotificationResponse,
      onDidReceiveBackgroundNotificationResponse:
          withdrawNotifyBackgroundResponse,
    );

    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    await android?.createNotificationChannel(
      const AndroidNotificationChannel(
        kWithdrawChannelId,
        kWithdrawChannelName,
        description: 'Pending withdraw order alerts',
        importance: Importance.high,
      ),
    );

    WithdrawNativeNotify.ensureOpenInboxListener(_onNativeOpenInbox);

    _initialized = true;
  }

  void _onNativeOpenInbox(String orderId) {
    final q = withdrawQueueProvider?.call();
    if (q != null && orderId.isNotEmpty) {
      q.setActive(orderId);
      unawaited(() async {
        try {
          await syncFromQueue(q, allowHeadsUp: false);
        } catch (_) {}
      }());
    }
    final open = onOpenWithdrawInbox;
    if (open != null) {
      open(orderId);
    } else {
      FlutterForegroundTask.sendDataToMain({
        'type': 'open_withdraw_inbox',
        'order_id': orderId,
      });
    }
  }

  /// Sync up to [kMaxShadeWithdrawNotifies] native shade children (+ group summary).
  /// Pass [wasEmpty] as the queue empty-state *before* the upsert that triggered sync.
  Future<void> syncFromQueue(
    WithdrawQueue q, {
    required bool allowHeadsUp,
    bool wasEmpty = false,
  }) async {
    await init();

    final pending = q.pending;
    if (pending.isEmpty) {
      await WithdrawNativeNotify.syncVisible(orders: const [], pendingCount: 0);
      await WithdrawNativeNotify.cancelAll();
      await _plugin.cancel(kWithdrawDetailNotifyId);
      await _plugin.cancel(kWithdrawSummaryNotifyId);
      return;
    }

    final now = _clock();
    final headsUp = allowHeadsUp &&
        shouldHeadsUp(
          wasEmpty: wasEmpty,
          lastHeadsUp: _lastHeadsUp,
          now: now,
        );
    if (headsUp) {
      _lastHeadsUp = now;
    }

    final visible = takeShadeWithdrawOrders(pending);
    // Newest pending gets heads-up when allowed (pending is newest-first).
    final headsUpOrderId = headsUp && visible.isNotEmpty ? visible.first.orderId : '';

    final orderMaps = <Map<String, Object?>>[];
    for (final o in visible) {
      orderMaps.add(<String, Object?>{
        'orderId': o.orderId,
        'amount': o.amount,
        'account': o.account,
        'bank': o.bank,
        'accountName': o.accountName,
        'body': formatWithdrawNotifyBody(
          amount: o.amount,
          account: o.account,
          bank: o.bank,
          accountName: o.accountName,
          approvedAt: o.approvedAt,
        ),
        'title': 'รายการถอนใหม่',
        'canCopy': q.canCopy(o.orderId),
      });
    }

    await WithdrawNativeNotify.syncVisible(
      orders: orderMaps,
      headsUpOrderId: headsUpOrderId,
      pendingCount: pending.length,
    );
    // Drop leftover FLN detail/summary — native owns the group now.
    await _plugin.cancel(kWithdrawDetailNotifyId);
    await _plugin.cancel(kWithdrawSummaryNotifyId);
  }

  void _onNotificationResponse(NotificationResponse response) {
    unawaited(_handleAction(response));
  }

  Future<void> _handleAction(NotificationResponse response) async {
    final actionId = response.actionId;
    final payload = response.payload;

    // Body tap (no action) → set active from payload + open inbox.
    if (actionId == null || actionId.isEmpty) {
      final q = withdrawQueueProvider?.call();
      final data = decodeWithdrawNotifyPayload(payload);
      final orderId =
          data?['order_id']?.trim() ?? (payload ?? '').trim();
      if (q != null && orderId.isNotEmpty) {
        q.setActive(orderId);
        try {
          await syncFromQueue(q, allowHeadsUp: false);
        } catch (_) {}
      }
      final open = onOpenWithdrawInbox;
      if (open != null) {
        open(orderId.isEmpty ? null : orderId);
      } else {
        FlutterForegroundTask.sendDataToMain({
          'type': 'open_withdraw_inbox',
          if (orderId.isNotEmpty) 'order_id': orderId,
        });
      }
      return;
    }

    // Phase A: native BroadcastReceiver owns shade copy actions. Ignore any
    // leftover FLN copy callbacks so we never double-write the clipboard.
    if (actionId == kCopyAmountActionId || actionId == kCopyAccountActionId) {
      return;
    }
  }

  /// Open inbox from a native detail notification body tap (intent extras),
  /// then fall through to FLN launch details (summary / legacy notifies).
  Future<void> handleLaunchDetails() async {
    await init();

    final nativeOrderId = await WithdrawNativeNotify.takeOpenInboxOrderId();
    if (nativeOrderId != null && nativeOrderId.isNotEmpty) {
      final q = withdrawQueueProvider?.call();
      if (q != null) {
        q.setActive(nativeOrderId);
        try {
          await syncFromQueue(q, allowHeadsUp: false);
        } catch (_) {}
      }
      final open = onOpenWithdrawInbox;
      if (open != null) {
        open(nativeOrderId);
      } else {
        FlutterForegroundTask.sendDataToMain({
          'type': 'open_withdraw_inbox',
          'order_id': nativeOrderId,
        });
      }
    }

    final details = await _plugin.getNotificationAppLaunchDetails();
    if (details?.didNotificationLaunchApp != true) return;
    final response = details!.notificationResponse;
    if (response == null) return;
    // Skip FLN copy actions (native owns copy); still handle body taps.
    final actionId = response.actionId;
    if (actionId == kCopyAmountActionId || actionId == kCopyAccountActionId) {
      return;
    }
    await _handleAction(response);
  }
}

@pragma('vm:entry-point')
void withdrawNotifyBackgroundResponse(NotificationResponse response) {
  // Phase A: native ClipboardCopyReceiver owns shade copy. No-op Dart background
  // copy to avoid double-handling / flaky isolate Clipboard.setData.
  // Body taps with showsUserInterface are delivered via launch details / FGS.
  final actionId = response.actionId;
  if (actionId == null || actionId.isEmpty) return;
  // Intentionally ignore copy_* (and any other) background actions.
}
