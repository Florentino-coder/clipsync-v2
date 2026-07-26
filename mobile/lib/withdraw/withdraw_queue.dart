import 'package:flutter/foundation.dart';

import 'withdraw_order.dart';

enum WithdrawItemState { pending, processing, done, failed }

class WithdrawQueue extends ChangeNotifier {
  final Map<String, WithdrawOrder> _orders = {};
  final Map<String, WithdrawItemState> _states = {};
  final Map<String, String> _failReasons = {};
  String? _activeOverride;

  List<WithdrawOrder> get pending {
    final list = _orders.values
        .where((o) => _states[o.orderId] == WithdrawItemState.pending)
        .toList();
    list.sort((a, b) => b.ts.compareTo(a.ts));
    return list;
  }

  /// Pending + processing + done + failed (newest first) for inbox display.
  List<WithdrawOrder> get visibleOrders {
    final list = _orders.values.toList();
    list.sort((a, b) => b.ts.compareTo(a.ts));
    return list;
  }

  WithdrawOrder? get active {
    final overrideId = _activeOverride;
    if (overrideId != null) {
      final o = _orders[overrideId];
      if (o != null && _states[overrideId] == WithdrawItemState.pending) {
        return o;
      }
    }
    final p = pending;
    if (p.isEmpty) return null;
    return p.first;
  }

  bool containsPending(String orderId) =>
      _states[orderId] == WithdrawItemState.pending;

  /// Returns true if this order_id was not already pending (new for heads-up).
  bool upsert(WithdrawOrder order) {
    final existing = _states[order.orderId];
    final wasNewPending = existing != WithdrawItemState.pending;
    _orders[order.orderId] = order;
    if (existing == WithdrawItemState.failed ||
        existing == WithdrawItemState.processing ||
        existing == WithdrawItemState.done) {
      // Keep safety state — silent re-notify must not unlock copy / heads-up.
      return false;
    }
    _states[order.orderId] = WithdrawItemState.pending;
    notifyListeners();
    return wasNewPending;
  }

  void setActive(String orderId) {
    _activeOverride = orderId;
    notifyListeners();
  }

  bool canCopy(String orderId) =>
      _states[orderId] == WithdrawItemState.pending;

  String? copyAmountText() {
    final o = active;
    if (o == null || !canCopy(o.orderId)) return null;
    return o.amount;
  }

  String? copyAccountText() {
    final o = active;
    if (o == null || !canCopy(o.orderId)) return null;
    return o.account;
  }

  void markProcessing(String orderId) {
    if (!_orders.containsKey(orderId)) return;
    _states[orderId] = WithdrawItemState.processing;
    if (_activeOverride == orderId) _activeOverride = null;
    notifyListeners();
  }

  void markSucceeded(String orderId) {
    if (!_orders.containsKey(orderId)) return;
    _states[orderId] = WithdrawItemState.done;
    if (_activeOverride == orderId) _activeOverride = null;
    notifyListeners();
  }

  void markDone(String orderId) {
    _orders.remove(orderId);
    _states.remove(orderId);
    _failReasons.remove(orderId);
    if (_activeOverride == orderId) _activeOverride = null;
    notifyListeners();
  }

  void markFailed(String orderId, {String reason = ''}) {
    if (!_orders.containsKey(orderId)) return;
    _states[orderId] = WithdrawItemState.failed;
    _failReasons[orderId] = reason;
    if (_activeOverride == orderId) _activeOverride = null;
    notifyListeners();
  }

  WithdrawItemState? stateOf(String orderId) => _states[orderId];

  /// Resolve queue order_id when slip_status carries amount push_id (e.g. `1900.00`).
  /// Returns null when none or more than one pending/processing row matches.
  String? findOrderIdByAmount(String amount) {
    final keys = _amountKeys(amount);
    if (keys.isEmpty) return null;
    String? found;
    for (final o in _orders.values) {
      final state = _states[o.orderId];
      if (state != WithdrawItemState.pending &&
          state != WithdrawItemState.processing) {
        continue;
      }
      final oKeys = _amountKeys(o.amount);
      if (oKeys.intersection(keys).isEmpty) continue;
      if (found != null) return null;
      found = o.orderId;
    }
    return found;
  }

  static Set<String> _amountKeys(String raw) {
    final text = raw.trim().replaceAll(',', '');
    if (text.isEmpty) return {};
    final keys = <String>{text};
    final parsed = double.tryParse(text);
    if (parsed != null) {
      keys.add(parsed.toStringAsFixed(2));
    }
    return keys;
  }

  void clearPending() {
    final ids = _orders.keys
        .where((id) => _states[id] == WithdrawItemState.pending)
        .toList(growable: false);
    for (final id in ids) {
      markDone(id);
    }
  }
}
