import { t } from '@lingui/core/macro';
import { Button, Group, Modal, NumberInput, Stack, Text } from '@mantine/core';
import { IconFileSpreadsheet } from '@tabler/icons-react';
import { useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import { downloadApiFile } from '../../functions/download';

/*
 * Ask how many assemblies to build, then download the raw materials
 * required to build them from scratch (from the BOM) as an Excel file.
 */
export function usePartRawMaterialsDialog({ partId }: { partId: number }) {
  const api = useApi();
  const [opened, setOpened] = useState(false);
  const [quantity, setQuantity] = useState<number | string>(1);
  const [loading, setLoading] = useState(false);

  const valid = Number(quantity) > 0;

  const download = () => {
    setLoading(true);
    downloadApiFile({
      api: api,
      url: apiUrl(ApiEndpoints.part_raw_materials, partId),
      params: { quantity: quantity },
      fallbackName: 'raw_materials.xlsx'
    }).finally(() => {
      setLoading(false);
      setOpened(false);
    });
  };

  const dialog = (
    <Modal
      opened={opened}
      onClose={() => setOpened(false)}
      title={t`Raw Materials`}
    >
      <Stack>
        <Text size='sm'>
          {t`Purchased parts required to build this quantity from scratch, breaking down every sub-assembly through its BOM. Setup quantity, attrition and rounding are included.`}
        </Text>
        <NumberInput
          label={t`Quantity to build`}
          value={quantity}
          onChange={setQuantity}
          min={0}
          decimalScale={5}
          error={valid ? undefined : t`Quantity must be greater than zero`}
          data-autofocus
        />
        <Group justify='right'>
          <Button variant='outline' onClick={() => setOpened(false)}>
            {t`Cancel`}
          </Button>
          <Button
            leftSection={<IconFileSpreadsheet />}
            onClick={download}
            loading={loading}
            disabled={!valid}
          >
            {t`Export`}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );

  return { open: () => setOpened(true), dialog };
}
