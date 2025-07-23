import React from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
} from '@mui/material';

interface AboutDialogProps {
  open: boolean;
  onClose: () => void;
}

const AboutDialog: React.FC<AboutDialogProps> = ({ open, onClose }) => {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ textAlign: 'center', pb: 1 }}>
        <Typography variant="h5" component="div" color="primary" fontWeight="bold">
          MCP Client for Azure
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ textAlign: 'center', py: 2 }}>
        <Box>
          <Typography variant="body1" color="text.secondary" sx={{ mb: 2 }}>
            by Keisuke Hatasaki
          </Typography>
          <Typography 
            variant="body2" 
            color="text.secondary" 
            sx={{ 
              fontSize: '0.85rem',
              fontStyle: 'italic',
              opacity: 0.8
            }}
          >
            (This is not an official application, use only for private/testing at your own risk.)
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions sx={{ justifyContent: 'center', pb: 2 }}>
        <Button onClick={onClose} variant="contained" color="primary">
          OK
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default AboutDialog;
