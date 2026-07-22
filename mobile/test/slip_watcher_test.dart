import 'package:clipsync_app/slip/slip_watcher.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('SlipWatcher exposes clipsync/slip_events channel', () {
    expect(SlipWatcher.channelName, 'clipsync/slip_events');
    expect(SlipWatcher(), isA<SlipWatcher>());
  });

  test('mapEvent copies path/bucket/date_added from native payload', () {
    final mapped = SlipWatcher.mapEvent({
      'uri': 'content://media/external/images/media/42',
      'path': '/storage/emulated/0/DCIM/SCB Easy/slip.jpg',
      'bucket': 'SCB Easy',
      'relative_path': 'DCIM/SCB Easy/',
      'date_added': 1721640000,
    });

    expect(mapped['path'], '/storage/emulated/0/DCIM/SCB Easy/slip.jpg');
    expect(mapped['bucket'], 'SCB Easy');
    expect(mapped['date_added'], 1721640000);
    expect(mapped['uri'], 'content://media/external/images/media/42');
    expect(mapped['relative_path'], 'DCIM/SCB Easy/');
  });
}
