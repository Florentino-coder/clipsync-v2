import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'bank_logos.dart';
import 'withdraw_notify_service.dart';
import 'withdraw_order.dart';
import 'withdraw_queue.dart';

/// In-app pending withdraw inbox (newest first). Required for Phase A ship.
class WithdrawInboxPage extends StatefulWidget {
  const WithdrawInboxPage({
    super.key,
    required this.queue,
    this.onActiveChanged,
    this.onCopied,
    this.onCleared,
  });

  final WithdrawQueue queue;

  /// Called after [WithdrawQueue.setActive] so caller can refresh the notification.
  final Future<void> Function(WithdrawOrder order)? onActiveChanged;

  /// Toast / snackbar after per-row copy.
  final void Function(String label, String text)? onCopied;

  /// Called after pending queue is cleared (e.g. refresh notification).
  final Future<void> Function()? onCleared;

  @override
  State<WithdrawInboxPage> createState() => _WithdrawInboxPageState();
}

class _WithdrawInboxPageState extends State<WithdrawInboxPage> {
  String _stateLabel(WithdrawItemState? state) {
    switch (state) {
      case WithdrawItemState.pending:
        return 'รอโอน';
      case WithdrawItemState.processing:
        return 'กำลังดำเนินการ';
      case WithdrawItemState.done:
        return 'สำเร็จ';
      case WithdrawItemState.failed:
        return 'ไม่สำเร็จ';
      case null:
        return '';
    }
  }

  Future<void> _copy(String label, String text) async {
    widget.onCopied?.call(label, text);
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    if (widget.onCopied == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('$label: $text'),
          duration: const Duration(seconds: 2),
        ),
      );
    }
  }

  Future<void> _onRowTap(WithdrawOrder order) async {
    widget.queue.setActive(order.orderId);
    setState(() {});
    await widget.onActiveChanged?.call(order);
  }

  Future<void> _confirmClearPending() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: const Text('ล้างรายการถอนรอโอนทั้งหมด?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('ยกเลิก'),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('เคลียร์'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    widget.queue.clearPending();
    setState(() {});
    await widget.onCleared?.call();
  }

  @override
  Widget build(BuildContext context) {
    final items = widget.queue.visibleOrders;
    final activeId = widget.queue.active?.orderId;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text('รายการถอนรอโอน'),
        actions: [
          if (widget.queue.pending.isNotEmpty)
            TextButton(
              onPressed: () => unawaited(_confirmClearPending()),
              child: const Text('เคลียร์งาน'),
            ),
        ],
      ),
      body: items.isEmpty
          ? Center(
              child: Text(
                'ไม่มีรายการถอนรอโอน',
                style: TextStyle(
                  fontSize: 16,
                  color: cs.onSurfaceVariant,
                ),
              ),
            )
          : ListView.separated(
              padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 8),
              itemCount: items.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final order = items[index];
                final canCopy = widget.queue.canCopy(order.orderId);
                final isActive = order.orderId == activeId;
                final state = widget.queue.stateOf(order.orderId);
                final lines = formatWithdrawInboxLines(
                  amount: order.amount,
                  account: order.account,
                  bank: order.bank,
                  accountName: order.accountName,
                  stateLabel: _stateLabel(state),
                );
                final amountLine = lines.isNotEmpty ? lines.first : '💰 ยอด: ${order.amount}';
                final detailLines = lines.length > 1 ? lines.sublist(1) : const <String>[];

                return ListTile(
                  selected: isActive,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 8,
                  ),
                  leading: Image.asset(
                    bankLogoAsset(order.bank),
                    width: 40,
                    height: 40,
                    errorBuilder: (_, __, ___) =>
                        const Icon(Icons.account_balance),
                  ),
                  title: Text(
                    amountLine,
                    style: const TextStyle(
                      fontWeight: FontWeight.w700,
                      fontSize: 18,
                    ),
                  ),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final line in detailLines)
                        Padding(
                          padding: const EdgeInsets.only(top: 2),
                          child: Text(line),
                        ),
                      Wrap(
                        spacing: 4,
                        children: [
                          TextButton(
                            style: TextButton.styleFrom(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 8,
                                vertical: 8,
                              ),
                            ),
                            onPressed: canCopy
                                ? () => unawaited(
                                      _copy('คัดลอกยอดแล้ว', order.amount),
                                    )
                                : null,
                            child: const Text('คัดลอกยอด'),
                          ),
                          TextButton(
                            style: TextButton.styleFrom(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 8,
                                vertical: 8,
                              ),
                            ),
                            onPressed: canCopy
                                ? () => unawaited(
                                      _copy('คัดลอกบัญชีแล้ว', order.account),
                                    )
                                : null,
                            child: const Text('คัดลอกบัญชี'),
                          ),
                        ],
                      ),
                    ],
                  ),
                  isThreeLine: true,
                  onTap: () => unawaited(_onRowTap(order)),
                );
              },
            ),
    );
  }
}
