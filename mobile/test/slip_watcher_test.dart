import 'package:clipsync_app/slip/slip_watcher.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('SlipWatcher exposes clipsync/slip_events channel', () {
    expect(SlipWatcher.channelName, 'clipsync/slip_events');
    expect(SlipWatcher(), isA<SlipWatcher>());
  });
}
