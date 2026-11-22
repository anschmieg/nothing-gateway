import unittest
from unittest.mock import Mock
import wireguard_endpoint as w


class EndpointTests(unittest.TestCase):
    def fixtures(self, stamp='1', endpoint='192.0.2.1:56107'):
        def output(args, **kwargs):
            if args[1]=='set':return ''
            return 'public-peer\t'+{'latest-handshakes':stamp,'endpoints':endpoint}[args[-1]]+'\n'
        run=Mock(side_effect=output)
        config=Mock();config.read_text.return_value='[Interface]\nPrivateKey = never-log-this\n[Peer]\nEndpoint = home.example:56107\n'
        resolve=Mock(return_value=[(None,None,None,None,('192.0.2.2',56107))])
        return run,config,resolve

    def test_stale_endpoint_updated(self):
        run,config,resolve=self.fixtures()
        self.assertTrue(w.refresh(now=1000,run=run,resolve=resolve,config_path=config))
        self.assertEqual(run.call_args.args[0],['wg','set','heretic-home','peer','public-peer','endpoint','192.0.2.2:56107'])

    def test_recent_roaming_peer_left_alone(self):
        run,config,resolve=self.fixtures(stamp='999')
        self.assertFalse(w.refresh(now=1000,run=run,resolve=resolve,config_path=config))
        resolve.assert_not_called();config.read_text.assert_not_called()

    def test_current_endpoint_no_change(self):
        run,config,resolve=self.fixtures(endpoint='192.0.2.2:56107')
        self.assertFalse(w.refresh(now=1000,run=run,resolve=resolve,config_path=config))
        self.assertFalse(any(c.args[0][1]=='set' for c in run.call_args_list))

    def test_dns_failure_retains_endpoint(self):
        run,config,resolve=self.fixtures();resolve.side_effect=OSError('DNS unavailable')
        with self.assertRaises(OSError):w.refresh(now=1000,run=run,resolve=resolve,config_path=config)
        self.assertFalse(any(c.args[0][1]=='set' for c in run.call_args_list))

    def test_multiple_peers_refused(self):
        run,config,resolve=self.fixtures();run.side_effect=None
        run.return_value='first\t1\nsecond\t1\n'
        with self.assertRaises(RuntimeError):w.refresh(now=1000,run=run,resolve=resolve,config_path=config)
        resolve.assert_not_called()

if __name__=='__main__':unittest.main()
