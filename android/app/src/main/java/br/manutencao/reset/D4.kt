package br.manutencao.reset

/**
 * IEEE 1284.4 ("D4") mínimo — a mesma sequência que o apdadrv.dll do AdjProg envia
 * (e que o programa do Windows usa, validada em L3150/L3250/L3110/L375).
 * Pacote: PSID SSID LEN(2, big-endian, inclui cabeçalho) CREDIT CONTROL + dados.
 */
interface Porta {
    fun write(dados: ByteArray): Int
    fun read(timeoutMs: Int): ByteArray
}

/** Canal que leva um comando EPSON-CTRL e devolve a resposta (USB via D4, ou rede via SNMP). */
interface CanalCtrl {
    fun transact(payload: ByteArray): ByteArray
}

class D4Erro(msg: String, val etapa: String = "comunicacao") : Exception(msg)

class D4Link(private val porta: Porta) : CanalCtrl {
    companion object {
        val ENTER_D4 = byteArrayOf(0, 0, 0, 0x1b, 0x01) + "@EJL 1284.4\n@EJL\n@EJL\n".toByteArray(Charsets.US_ASCII)
        const val CTRL = 0x02
    }

    private var rev = 0x10
    private var buf = ByteArray(0)
    private val credito = mutableMapOf<Int, Int>()
    private val abertos = mutableSetOf<Int>()

    private fun enviarPacote(sid: Int, dados: ByteArray, credit: Int = 1) {
        val n = 6 + dados.size
        val cab = byteArrayOf(sid.toByte(), sid.toByte(), (n shr 8).toByte(), n.toByte(), credit.toByte(), 0)
        porta.write(cab + dados)
    }

    private fun receberPacote(tentativas: Int = 8): Pair<Int, ByteArray>? {
        repeat(tentativas) {
            if (buf.size >= 6) {
                val ln = ((buf[2].toInt() and 0xFF) shl 8) or (buf[3].toInt() and 0xFF)
                if (ln < 6) buf = ByteArray(0)
                else if (buf.size >= ln) {
                    val pkt = buf.copyOfRange(0, ln)
                    buf = buf.copyOfRange(ln, buf.size)
                    val sid = pkt[0].toInt() and 0xFF
                    val cr = pkt[4].toInt() and 0xFF
                    if (cr > 0) credito[sid] = (credito[sid] ?: 0) + cr
                    return sid to pkt.copyOfRange(6, pkt.size)
                }
            }
            val pedaco = porta.read(1500)
            if (pedaco.isNotEmpty()) buf += pedaco
        }
        return null
    }

    private fun txn(dados: ByteArray, espera: Int): ByteArray {
        enviarPacote(0, dados)
        repeat(6) {
            val (sid, p) = receberPacote() ?: throw D4Erro("Sem resposta ao comando D4 0x%02x".format(espera and 0x7F))
            if (sid == 0 && p.isNotEmpty()) {
                val c = p[0].toInt() and 0xFF
                if (c == espera) return p
                if (c == 0x7F) throw D4Erro("Erro D4 da impressora")
            }
        }
        throw D4Erro("Sem resposta ao comando D4 0x%02x".format(espera and 0x7F))
    }

    fun entrar() {
        porta.write(ENTER_D4)
        Thread.sleep(200)
        porta.read(1500)
        for (r in intArrayOf(0x10, 0x20)) {
            buf = ByteArray(0)
            val p = try { txn(byteArrayOf(0x00, r.toByte()), 0x80) } catch (e: D4Erro) { continue }
            if (p.size > 1 && p[1].toInt() == 0) { rev = r; return }
        }
        throw D4Erro("A impressora não entrou no modo de manutenção", etapa = "entrar")
    }

    fun abrir(sid: Int, tam: Int = 0x0200) {
        val b = mutableListOf(0x01, sid, sid, tam shr 8, tam and 0xFF, tam shr 8, tam and 0xFF, 0, 0)
        if (rev == 0x10) b += listOf(0, 0)
        val p = txn(ByteArray(b.size) { b[it].toByte() }, 0x81)
        if (p.size < 2 || p[1].toInt() != 0) throw D4Erro("Canal %d recusado".format(sid))
        abertos += sid
        credito.putIfAbsent(sid, 0)
    }

    private fun pedirCredito(sid: Int) {
        val dados = if (rev == 0x10)
            byteArrayOf(0x04, sid.toByte(), sid.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte())
        else byteArrayOf(0x04, sid.toByte(), sid.toByte(), 0, 0)
        val p = txn(dados, 0x84)
        if (p.size >= 6 && p[1].toInt() == 0) {
            val add = ((p[4].toInt() and 0xFF) shl 8) or (p[5].toInt() and 0xFF)
            credito[sid] = (credito[sid] ?: 0) + add
        }
    }

    fun transact(sid: Int, dados: ByteArray): ByteArray {
        if ((credito[sid] ?: 0) < 1) pedirCredito(sid)
        if ((credito[sid] ?: 0) < 1) throw D4Erro("Sem crédito no canal %d".format(sid))
        credito[sid] = (credito[sid] ?: 1) - 1
        enviarPacote(sid, dados, credit = 1)
        repeat(6) {
            val (rsid, p) = receberPacote() ?: throw D4Erro("Sem resposta no canal %d".format(sid))
            if (rsid == sid && p.isNotEmpty()) return p
        }
        throw D4Erro("Sem resposta no canal %d".format(sid))
    }

    override fun transact(payload: ByteArray): ByteArray = transact(CTRL, payload)

    fun fechar() {
        for (sid in abertos.toList()) {
            try {
                val d = if (rev == 0x10) byteArrayOf(0x02, sid.toByte(), sid.toByte(), 0) else byteArrayOf(0x02, sid.toByte(), sid.toByte())
                txn(d, 0x82)
            } catch (_: D4Erro) {
            }
            abertos -= sid
        }
        try { txn(byteArrayOf(0x08), 0x88) } catch (_: D4Erro) { }  // Exit: volta ao modo normal
    }
}
