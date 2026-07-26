import 'package:clipsync_app/withdraw/withdraw_native_notify.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('com.clipsync.mobile_build/withdraw_notify');
  final log = <MethodCall>[];

  setUp(() {
    log.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      log.add(call);
      return null;
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('takeShadeWithdrawOrders caps at 20 newest', () {
    final ids = List.generate(25, (i) => 'ORD-$i');
    final taken = takeShadeWithdrawOrders(ids);
    expect(taken, hasLength(20));
    expect(taken.first, 'ORD-0');
    expect(taken.last, 'ORD-19');
  });

  test('WithdrawNativeNotify.syncVisible sends capped orders list', () async {
    final orders = List.generate(
      22,
      (i) => <String, Object?>{
        'orderId': 'ORD-$i',
        'amount': '$i.00',
        'account': '100$i',
        'bank': 'KBANK',
        'accountName': 'A',
        'body': 'body-$i',
        'title': 'รายการถอนใหม่',
        'canCopy': true,
      },
    );

    await WithdrawNativeNotify.syncVisible(
      orders: orders,
      headsUpOrderId: 'ORD-0',
      pendingCount: 22,
    );

    expect(log, hasLength(1));
    expect(log.single.method, 'syncVisible');
    final args = log.single.arguments as Map;
    expect(args['headsUpOrderId'], 'ORD-0');
    expect(args['pendingCount'], 22);
    final sent = args['orders'] as List;
    expect(sent, hasLength(20));
    expect((sent.first as Map)['orderId'], 'ORD-0');
    expect((sent.last as Map)['orderId'], 'ORD-19');
  });

  test('WithdrawNativeNotify.show routes through syncVisible', () async {
    await WithdrawNativeNotify.show(
      orderId: 'ORD-1',
      amount: '1.00',
      account: '020323427136',
      bank: 'KBANK',
      accountName: 'ทดสอบ',
      body: '💰 ยอด: 1.00\n🏦 บัญชี: 020323427136',
      title: 'รายการถอนใหม่',
      canCopy: true,
      headsUp: true,
      pendingCount: 2,
    );

    expect(log, hasLength(1));
    expect(log.single.method, 'syncVisible');
    final args = log.single.arguments as Map;
    expect(args['headsUpOrderId'], 'ORD-1');
    expect(args['pendingCount'], 2);
    final sent = args['orders'] as List;
    expect(sent, hasLength(1));
    expect(sent.single, {
      'orderId': 'ORD-1',
      'amount': '1.00',
      'account': '020323427136',
      'bank': 'KBANK',
      'accountName': 'ทดสอบ',
      'body': '💰 ยอด: 1.00\n🏦 บัญชี: 020323427136',
      'title': 'รายการถอนใหม่',
      'canCopy': true,
    });
  });

  test('WithdrawNativeNotify.cancel and cancelAll encode methods', () async {
    await WithdrawNativeNotify.cancel(id: 41001);
    await WithdrawNativeNotify.cancelAll();

    expect(log.map((c) => c.method), ['cancel', 'cancelAll']);
    expect(log.first.arguments, {'id': 41001});
    expect(log.last.arguments, isNull);
  });

  test('WithdrawNativeNotify.takeOpenInboxOrderId returns channel value', () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      log.add(call);
      if (call.method == 'takeOpenInboxOrderId') return 'ORD-OPEN';
      return null;
    });

    final orderId = await WithdrawNativeNotify.takeOpenInboxOrderId();
    expect(orderId, 'ORD-OPEN');
    expect(log.single.method, 'takeOpenInboxOrderId');
  });

  test('syncVisible swallows PlatformException', () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      throw PlatformException(code: 'no_context', message: 'plugin not attached');
    });
    await WithdrawNativeNotify.syncVisible(
      orders: [
        {
          'orderId': 'ORD-1',
          'amount': '100.00',
          'account': '0618407497',
          'bank': 'KBANK',
          'accountName': 'สมชาย',
          'body': 'x',
          'title': 'รายการถอนใหม่',
          'canCopy': true,
        }
      ],
      headsUpOrderId: 'ORD-1',
      pendingCount: 1,
    ); // must complete without throw
  });
}
