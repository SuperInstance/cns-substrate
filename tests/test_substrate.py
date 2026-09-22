"""Tests for the substrate-aware CNS bus."""
import unittest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cns_bridge.substrate import SubstratePacket, SubstrateBus, fnv1a_64, example
from cns_bridge.packet import Header, Packet
from cns_bridge.protocol import Intent, Priority


class TestFNV1a(unittest.TestCase):
    def test_fleet_canary(self):
        self.assertEqual(fnv1a_64('café Δ 日本語'), '0x24a555471370b18d')


class TestSubstrateBus(unittest.TestCase):
    def test_creation(self):
        bus = SubstrateBus(bus_id='test')
        self.assertEqual(len(bus.cells), 0)
        self.assertEqual(bus.prev_hash, '0x0000000000000000')
    
    def test_send(self):
        bus = SubstrateBus(bus_id='test')
        sp = bus.send(origin_id='test', body={'msg': 'hello'})
        self.assertEqual(len(bus.cells), 1)
        self.assertNotEqual(bus.prev_hash, '0x0000000000000000')
    
    def test_chain(self):
        bus = SubstrateBus(bus_id='test')
        sp1 = bus.send(origin_id='a', body={})
        sp2 = bus.send(origin_id='b', body={})
        sp3 = bus.send(origin_id='c', body={})
        self.assertEqual(sp1.prev_hash, '0x0000000000000000')
        self.assertEqual(sp2.prev_hash, sp1.hash)
        self.assertEqual(sp3.prev_hash, sp2.hash)
    
    def test_example(self):
        report = example()
        self.assertEqual(report['num_cells'], 3)
        self.assertEqual(len(report['cells']), 3)


class TestSubstratePacket(unittest.TestCase):
    def test_hash_stable(self):
        header = Header(origin_id='test', intent=Intent.QUERY)
        packet = Packet(header=header, body={'msg': 'hello'})
        
        sp1 = SubstratePacket(
            packet=packet,
            cell_id='c1',
            prev_hash='0x0',
            timestamp=12345.0,
        )
        sp2 = SubstratePacket(
            packet=packet,
            cell_id='c1',
            prev_hash='0x0',
            timestamp=12345.0,
        )
        self.assertEqual(sp1.hash, sp2.hash)


if __name__ == '__main__':
    unittest.main(verbosity=2)
